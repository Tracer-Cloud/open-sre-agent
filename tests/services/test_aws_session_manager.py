import threading
import time
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.services.aws_session_manager import AWSSessionManager


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the AWSSessionManager singleton and its class lock before each test."""
    AWSSessionManager._instance = None
    AWSSessionManager._lock = threading.Lock()
    yield


def test_session_isolation_by_external_id():
    """
    CRITICAL SECURITY TEST: Ensure that different external_ids for the same role_arn
    result in separate AssumeRole calls and separate sessions.
    """
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/TestRole"
    region = "us-east-1"

    # Mock STS client and assume_role response
    mock_sts = MagicMock()

    def mock_assume_role(**kwargs):
        ext_id = kwargs.get("ExternalId")
        return {
            "Credentials": {
                "AccessKeyId": f"AKIA-{ext_id}",
                "SecretAccessKey": f"SECRET-{ext_id}",
                "SessionToken": f"TOKEN-{ext_id}",
                "Expiration": MagicMock(timestamp=lambda: time.time() + 3600),
            }
        }

    mock_sts.assume_role.side_effect = mock_assume_role

    # Mock boto3.Session.client to return our mock STS
    with patch("boto3.Session") as mock_session_class:
        mock_base_session = MagicMock()
        mock_base_session.client.return_value = mock_sts
        mock_session_class.return_value = mock_base_session

        # Get sessions for different external IDs
        manager.get_session(region=region, role_arn=role_arn, external_id="ext_id_1")
        manager.get_session(region=region, role_arn=role_arn, external_id="ext_id_2")

        # VERIFY: Two separate calls were made to STS with different external IDs
        assert mock_sts.assume_role.call_count == 2
        mock_sts.assume_role.assert_any_call(
            RoleArn=role_arn, RoleSessionName=ANY, DurationSeconds=ANY, ExternalId="ext_id_1"
        )
        mock_sts.assume_role.assert_any_call(
            RoleArn=role_arn, RoleSessionName=ANY, DurationSeconds=ANY, ExternalId="ext_id_2"
        )

        # VERIFY: Sessions have different credentials
        mock_session_class.assert_any_call(
            aws_access_key_id="AKIA-ext_id_1",
            aws_secret_access_key="SECRET-ext_id_1",
            aws_session_token="TOKEN-ext_id_1",
            region_name=region,
        )
        mock_session_class.assert_any_call(
            aws_access_key_id="AKIA-ext_id_2",
            aws_secret_access_key="SECRET-ext_id_2",
            aws_session_token="TOKEN-ext_id_2",
            region_name=region,
        )


def test_get_client_double_check_locking():
    """Verify the double-check pattern in get_client."""
    manager = AWSSessionManager()
    service = "ec2"
    region = "us-east-1"

    # We want to simulate a race where two threads try to create the same client
    mock_session = MagicMock()
    mock_client = MagicMock()
    mock_session.client.return_value = mock_client

    with patch.object(manager, "get_session", return_value=mock_session):
        # First call creates the client
        client1 = manager.get_client(service, region=region)

        # Manually verify client is in cache
        cache_key = (service, region, None, None)
        assert manager._client_cache[cache_key] == mock_client

        # Second call should return cached client WITHOUT calling session.client again
        client2 = manager.get_client(service, region=region)

        assert client1 == client2
        assert mock_session.client.call_count == 1


def test_get_client_double_check_hit():
    """Verify the double-check branch in get_client when another thread wins the race."""
    manager = AWSSessionManager()
    service = "ec2"
    region = "us-east-1"
    cache_key = (service, region, None, None)

    # Mock get_session to fill the cache as a side effect (simulating another thread winning the race)
    winning_client = MagicMock()

    def side_effect(*args, **kwargs):
        manager._client_cache[cache_key] = winning_client
        return MagicMock()

    with patch.object(manager, "get_session", side_effect=side_effect):
        # Initial check misses, but second check (double-check) should hit
        client = manager.get_client(service, region=region)

        assert client is winning_client
        assert manager._client_cache[cache_key] is winning_client


def test_proactive_refresh():
    """Verify that a session near expiry (within 5 mins) is refreshed."""
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/TestRole"

    # 1. Setup an 'almost expired' session (e.g., expiring in 2 minutes)
    near_expiry = time.time() + 120  # 2 mins from now
    manager._session_metadata[(role_arn, None)] = {
        "access_key": "OLD_KEY",
        "secret_key": "OLD_SECRET",
        "session_token": "OLD_TOKEN",
        "expiration": near_expiry,
    }

    # 2. Mock STS to return a NEW session
    mock_sts = MagicMock()
    mock_sts.assume_role.return_value = {
        "Credentials": {
            "AccessKeyId": "NEW_KEY",
            "SecretAccessKey": "NEW_SECRET",
            "SessionToken": "NEW_TOKEN",
            "Expiration": MagicMock(timestamp=lambda: time.time() + 3600),
        }
    }

    with patch("boto3.Session") as mock_session_class:
        mock_base_session = MagicMock()
        mock_base_session.client.return_value = mock_sts
        mock_session_class.return_value = mock_base_session

        # 3. Call get_session — it should see the near-expiry and refresh
        manager.get_session(role_arn=role_arn)

        # VERIFY: New credentials are used
        mock_session_class.assert_any_call(
            aws_access_key_id="NEW_KEY",
            aws_secret_access_key="NEW_SECRET",
            aws_session_token="NEW_TOKEN",
            region_name=ANY,
        )
        assert mock_sts.assume_role.called


def test_assume_role_error_propagation():
    """Verify that STS errors are raised correctly."""
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/FailRole"

    from botocore.exceptions import ClientError

    mock_sts = MagicMock()
    error_response = {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}}
    mock_sts.assume_role.side_effect = ClientError(error_response, "AssumeRole")

    with patch("boto3.Session") as mock_session_class:
        mock_base_session = MagicMock()
        mock_base_session.client.return_value = mock_sts
        mock_session_class.return_value = mock_base_session

        with pytest.raises(ClientError, match="Access Denied"):
            manager.get_session(role_arn=role_arn)


def test_base_session_region_caching():
    """Verify that base sessions are cached per region."""
    manager = AWSSessionManager()

    with patch("boto3.Session") as mock_session_class:
        # Request session for region A twice
        manager.get_session(region="us-east-1")
        manager.get_session(region="us-east-1")
        # Request session for region B
        manager.get_session(region="us-west-2")

        # VERIFY: Only 2 Session objects created (one per region)
        assert mock_session_class.call_count == 2
        mock_session_class.assert_any_call(region_name="us-east-1")
        mock_session_class.assert_any_call(region_name="us-west-2")


def test_client_isolation_by_external_id():
    """Verify that different external IDs result in different cached clients."""
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/TestRole"
    service = "s3"

    with patch.object(manager, "get_session") as mock_get_session:
        # Mock two different sessions
        session1 = MagicMock()
        session2 = MagicMock()
        mock_get_session.side_effect = [session1, session2]

        client1 = manager.get_client(service, role_arn=role_arn, external_id="ext1")
        client2 = manager.get_client(service, role_arn=role_arn, external_id="ext2")

        assert client1 != client2
        assert mock_get_session.call_count == 2
        mock_get_session.assert_any_call(region=ANY, role_arn=role_arn, external_id="ext1")
        mock_get_session.assert_any_call(region=ANY, role_arn=role_arn, external_id="ext2")


def test_get_client_refreshes_expired_session():
    """Verify that get_client detects an expired session and creates a new client."""
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/TestRole"
    service = "sts"

    # 1. Setup a session that is already 'expired'
    manager._session_metadata[(role_arn, None)] = {
        "access_key": "OLD_KEY",
        "secret_key": "OLD_SECRET",
        "session_token": "OLD_TOKEN",
        "expiration": time.time() - 100,  # Expired
    }

    # Pre-populate client cache with a 'stale' client
    old_client = MagicMock()
    cache_key = (service, "us-east-1", role_arn, None)
    manager._client_cache[cache_key] = old_client

    # 2. Mock get_session to return a NEW session
    new_session = MagicMock()
    new_client = MagicMock()
    new_session.client.return_value = new_client

    with patch.object(manager, "get_session", return_value=new_session):
        # 3. Call get_client - it should see the expiry and NOT return old_client
        client = manager.get_client(service, region="us-east-1", role_arn=role_arn)

        assert client == new_client
        assert client != old_client
        assert manager._client_cache[cache_key] == new_client


def test_get_session_cache_hit():
    """Verify that get_session returns a cached session if it exists and is not expired."""
    manager = AWSSessionManager()
    role_arn = "arn:aws:iam::123456789012:role/TestRole"

    # 1. Setup a valid session
    manager._session_metadata[(role_arn, None)] = {
        "access_key": "VALID_KEY",
        "secret_key": "VALID_SECRET",
        "session_token": "VALID_TOKEN",
        "expiration": time.time() + 3600,
    }

    with patch("boto3.Session") as mock_session_class:
        # 2. Call get_session
        manager.get_session(role_arn=role_arn)

        # VERIFY: Session created using cached credentials
        mock_session_class.assert_any_call(
            aws_access_key_id="VALID_KEY",
            aws_secret_access_key="VALID_SECRET",
            aws_session_token="VALID_TOKEN",
            region_name=ANY,
        )


def test_is_session_expired_missing_metadata():
    """Verify that _is_session_expired returns True if metadata is missing or incomplete."""
    manager = AWSSessionManager()
    assert manager._is_session_expired("non-existent-role") is True

    # Metadata exists but missing expiration
    manager._session_metadata[("role", None)] = {"key": "val"}
    assert manager._is_session_expired("role") is True


def test_get_aws_session_manager_helper():
    """Verify the helper function returns the singleton instance."""
    from app.services.aws_session_manager import get_aws_session_manager

    m1 = get_aws_session_manager()
    m2 = get_aws_session_manager()
    assert m1 is m2
    assert isinstance(m1, AWSSessionManager)
