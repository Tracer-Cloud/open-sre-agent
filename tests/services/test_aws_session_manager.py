import time
from unittest.mock import ANY, MagicMock, patch

import pytest

from app.services.aws_session_manager import AWSSessionManager


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the AWSSessionManager singleton before each test."""
    AWSSessionManager._instance = None
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
