"""
AWS Session Manager for centralized session caching and atomic role locking.

Implements a thread-safe singleton to reuse boto3 clients and manage AssumeRole
operations efficiently, reducing latency and preventing throttling.

NOTE: This module uses a stateful singleton pattern which diverges from the
standard stateless integration pattern. This is a deliberate trade-off to
provide global connection pooling and prevent 'Thundering Herd' API
throttling across parallel investigation nodes.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

# Default session duration in seconds (1 hour)
DEFAULT_SESSION_DURATION = 3600
# Proactive refresh threshold (refresh 5 minutes before expiry)
REFRESH_THRESHOLD_SECONDS = 300


class AWSSessionManager:
    """
    Singleton manager for AWS sessions and clients.

    Caches:
    - Base sessions (per region)
    - Assumed role sessions (per role_arn + region)
    - Service clients (per service + region + role_arn)
    """

    _instance: AWSSessionManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> AWSSessionManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_manager()
        return cls._instance

    def _init_manager(self) -> None:
        """Initialize the manager's internal state."""
        # Key: (service_name, region, role_arn, external_id)
        self._client_cache: dict[tuple[str, str, str | None, str | None], Any] = {}
        # Key: (role_arn, external_id)
        self._session_metadata: dict[tuple[str, str | None], dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        # Key: (role_arn, external_id)
        self._role_locks: dict[tuple[str, str | None], threading.Lock] = {}
        self._base_sessions: dict[str, boto3.Session] = {}

    def _get_role_lock(self, role_arn: str, external_id: str | None) -> threading.Lock:
        """Get or create a lock for a specific role ARN + external ID to ensure atomic assumption."""
        key = (role_arn, external_id)
        with self._cache_lock:
            if key not in self._role_locks:
                self._role_locks[key] = threading.Lock()
            return self._role_locks[key]

    def _is_session_expired(self, role_arn: str, external_id: str | None) -> bool:
        """
        Check if an assumed role session is expired or near expiry.
        Must be called while holding _cache_lock or role_lock if consistency is required.
        """
        key = (role_arn, external_id)
        metadata = self._session_metadata.get(key)
        if not metadata:
            return True

        expiration = metadata.get("expiration")
        if not expiration:
            return True

        # Proactive refresh: check if we are within REFRESH_THRESHOLD_SECONDS of expiry
        return bool(time.time() > (float(expiration) - REFRESH_THRESHOLD_SECONDS))

    def get_session(
        self, region: str | None = None, role_arn: str | None = None, external_id: str | None = None
    ) -> boto3.Session:
        """
        Get a cached or new boto3 Session.

        Args:
            region: AWS region name
            role_arn: Optional IAM role ARN to assume
            external_id: Optional external ID for AssumeRole

        Returns:
            boto3.Session object
        """
        actual_region: str = (
            region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        )

        if not role_arn:
            with self._cache_lock:
                if actual_region not in self._base_sessions:
                    self._base_sessions[actual_region] = boto3.Session(region_name=actual_region)
                return self._base_sessions[actual_region]

        # Handle Role Assumption with Atomic Locking
        role_lock = self._get_role_lock(role_arn, external_id)
        with role_lock:
            # Check expiry under lock to prevent race conditions
            key = (role_arn, external_id)
            with self._cache_lock:
                is_expired = self._is_session_expired(role_arn, external_id)
                if not is_expired:
                    metadata = self._session_metadata[key]
                    return boto3.Session(
                        aws_access_key_id=metadata["access_key"],
                        aws_secret_access_key=metadata["secret_key"],
                        aws_session_token=metadata["session_token"],
                        region_name=actual_region,
                    )

            # Session expired or doesn't exist, assume role
            logger.info("Assuming role: %s", role_arn)
            # Use base session to get STS client
            base_session = self.get_session(region=actual_region)
            sts = base_session.client("sts")

            assume_role_kwargs: dict[str, Any] = {
                "RoleArn": role_arn,
                "RoleSessionName": f"OpenSRE-Session-{int(time.time())}",
                "DurationSeconds": DEFAULT_SESSION_DURATION,
            }
            if external_id:
                assume_role_kwargs["ExternalId"] = external_id

            try:
                response = sts.assume_role(**assume_role_kwargs)
                credentials = response["Credentials"]

                # Update metadata in memory only
                key = (role_arn, external_id)
                with self._cache_lock:
                    self._session_metadata[key] = {
                        "access_key": credentials["AccessKeyId"],
                        "secret_key": credentials["SecretAccessKey"],
                        "session_token": credentials["SessionToken"],
                        "expiration": credentials["Expiration"].timestamp(),
                    }

                return boto3.Session(
                    aws_access_key_id=credentials["AccessKeyId"],
                    aws_secret_access_key=credentials["SecretAccessKey"],
                    aws_session_token=credentials["SessionToken"],
                    region_name=actual_region,
                )
            except ClientError as e:
                logger.error("Failed to assume role %s: %s", role_arn, e)
                raise

    def get_client(
        self,
        service_name: str,
        region: str | None = None,
        role_arn: str | None = None,
        external_id: str | None = None,
    ) -> Any:
        """
        Get a cached or new boto3 client.

        Args:
            service_name: AWS service name (e.g., 'ec2')
            region: AWS region name
            role_arn: Optional IAM role ARN to assume
            external_id: Optional external ID for AssumeRole

        Returns:
            boto3 client instance
        """
        actual_region = (
            region or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
        )
        # Fixes P1: Include external_id in cache key
        cache_key = (service_name, actual_region, role_arn, external_id)

        with self._cache_lock:
            # Check if we have a valid cached client
            if cache_key in self._client_cache and (
                not role_arn or not self._is_session_expired(role_arn, external_id)
            ):
                return self._client_cache[cache_key]

        # Create new client (network bound)
        session = self.get_session(region=actual_region, role_arn=role_arn, external_id=external_id)

        # Use persistent connections configuration
        config = Config(
            retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=60
        )

        client = session.client(service_name, config=config)  # type: ignore[call-overload]

        with self._cache_lock:
            # Fixes P1: Double-check pattern to prevent race-overwrites after network call
            if cache_key in self._client_cache and (
                not role_arn or not self._is_session_expired(role_arn, external_id)
            ):
                return self._client_cache[cache_key]

            self._client_cache[cache_key] = client
            return client


def get_aws_session_manager() -> AWSSessionManager:
    """Helper to get the singleton manager instance."""
    return AWSSessionManager()
