"""
AWS Session Manager for centralized session caching and atomic role locking.

Implements a thread-safe singleton to reuse boto3 clients and manage AssumeRole
operations efficiently, reducing latency and preventing throttling.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Optional

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

    _instance: Optional["AWSSessionManager"] = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init_manager()
        return cls._instance

    def _init_manager(self):
        """Initialize the manager's internal state."""
        self._client_cache: dict[tuple[str, str | None, str | None], Any] = {}
        self._session_metadata: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()
        self._role_locks: dict[str, threading.Lock] = {}
        self._base_sessions: dict[str, boto3.Session] = {}

        # Metadata persistence path
        self._cache_dir = os.path.expanduser("~/.opensre/cache")
        self._metadata_path = os.path.join(self._cache_dir, "aws_session_metadata.json")

        # Create cache directory if it doesn't exist
        try:
            os.makedirs(self._cache_dir, exist_ok=True)
            self._load_metadata()
        except Exception as e:
            logger.warning("Could not initialize AWS session cache directory: %s", e)

    def _load_metadata(self):
        """Load session metadata from disk using JSON."""
        if os.path.exists(self._metadata_path):
            try:
                with open(self._metadata_path) as f:
                    self._session_metadata = json.load(f)
            except Exception as e:
                logger.warning("Could not load AWS session metadata: %s", e)
                self._session_metadata = {}

    def _save_metadata(self):
        """Save session metadata to disk using JSON."""
        try:
            with open(self._metadata_path, "w") as f:
                json.dump(self._session_metadata, f, indent=2)
        except Exception as e:
            logger.warning("Could not save AWS session metadata: %s", e)

    def _get_role_lock(self, role_arn: str) -> threading.Lock:
        """Get or create a lock for a specific role ARN to ensure atomic assumption."""
        with self._cache_lock:
            if role_arn not in self._role_locks:
                self._role_locks[role_arn] = threading.Lock()
            return self._role_locks[role_arn]

    def _is_session_expired(self, role_arn: str) -> bool:
        """Check if an assumed role session is expired or near expiry."""
        metadata = self._session_metadata.get(role_arn)
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
        role_lock = self._get_role_lock(role_arn)
        with role_lock:
            if not self._is_session_expired(role_arn):
                metadata = self._session_metadata[role_arn]
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
            }
            if external_id:
                assume_role_kwargs["ExternalId"] = external_id

            try:
                response = sts.assume_role(**assume_role_kwargs)
                credentials = response["Credentials"]

                # Update metadata
                self._session_metadata[role_arn] = {
                    "access_key": credentials["AccessKeyId"],
                    "secret_key": credentials["SecretAccessKey"],
                    "session_token": credentials["SessionToken"],
                    "expiration": credentials["Expiration"].timestamp(),
                }
                self._save_metadata()

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
        cache_key = (service_name, actual_region, role_arn)

        with self._cache_lock:
            # Check if we have a valid cached client
            if cache_key in self._client_cache and (
                not role_arn or not self._is_session_expired(role_arn)
            ):
                return self._client_cache[cache_key]

        # Create new client
        session = self.get_session(region=actual_region, role_arn=role_arn, external_id=external_id)

        # Use persistent connections configuration
        config = Config(
            retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=60
        )

        client = session.client(service_name, config=config)  # type: ignore[call-overload]

        with self._cache_lock:
            self._client_cache[cache_key] = client
            return client


def get_aws_session_manager() -> AWSSessionManager:
    """Helper to get the singleton manager instance."""
    return AWSSessionManager()
