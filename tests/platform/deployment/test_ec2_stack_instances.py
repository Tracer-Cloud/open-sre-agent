from __future__ import annotations

from unittest.mock import MagicMock, patch

from platform.deployment.aws.ec2 import find_stack_instance_ids


@patch("platform.deployment.aws.ec2.get_boto3_client")
def test_find_stack_instance_ids_returns_sorted_active_instances(
    mock_get_boto3_client: MagicMock,
) -> None:
    ec2 = MagicMock()
    ec2.describe_instances.return_value = {
        "Reservations": [
            {"Instances": [{"InstanceId": "i-bbb"}]},
            {"Instances": [{"InstanceId": "i-aaa"}]},
        ]
    }
    mock_get_boto3_client.return_value = ec2

    result = find_stack_instance_ids("opensre-ec2", region="us-east-1")

    assert result == ["i-aaa", "i-bbb"]
    ec2.describe_instances.assert_called_once()
