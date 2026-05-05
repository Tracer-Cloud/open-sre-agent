"""Tests for the RDS investigation tools."""

from __future__ import annotations

import logging
from unittest.mock import patch

from app.tools.RDSDescribeInstanceTool import describe_rds_instance
from app.tools.RDSEventsTool import describe_rds_events


@patch("app.tools.RDSDescribeInstanceTool.execute_aws_sdk_call")
def test_describe_rds_instance_success(mock_call) -> None:
    mock_call.return_value = {
        "success": True,
        "data": {
            "DBInstances": [
                {
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                    "EngineVersion": "15.4",
                    "DBInstanceClass": "db.t4g.micro",
                    "MultiAZ": True,
                    "PubliclyAccessible": False,
                    "StorageType": "gp3",
                    "AllocatedStorage": 20,
                    "Endpoint": {"Address": "prod.abc.rds.aws", "Port": 5432},
                    "AvailabilityZone": "us-east-1a",
                    "PreferredBackupWindow": "03:00-04:00",
                    "BackupRetentionPeriod": 7,
                }
            ]
        },
    }

    result = describe_rds_instance("prod-db", region="us-east-1")
    assert result["available"] is True
    assert result["status"] == "available"
    assert result["engine"] == "postgres"
    assert result["multi_az"] is True
    assert result["endpoint"]["port"] == 5432


@patch("app.tools.RDSDescribeInstanceTool.execute_aws_sdk_call")
def test_describe_rds_instance_not_found(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"DBInstances": []}}

    result = describe_rds_instance("missing-db")
    assert result["available"] is False
    assert "No RDS instance" in result["error"]


@patch("app.tools.RDSDescribeInstanceTool.execute_aws_sdk_call")
def test_describe_rds_instance_aws_failure(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "AccessDenied"}

    result = describe_rds_instance("prod-db")
    assert result["available"] is False
    assert result["error"] == "Failed to describe the RDS instance. Check server logs for details."


@patch("app.tools.RDSDescribeInstanceTool.execute_aws_sdk_call")
def test_describe_rds_instance_multiple_instances_warns(mock_call, caplog) -> None:
    """When AWS returns >1 instance, use the first and emit a warning."""
    mock_call.return_value = {
        "success": True,
        "data": {
            "DBInstances": [
                {
                    "DBInstanceStatus": "available",
                    "Engine": "postgres",
                    "EngineVersion": "15.4",
                    "DBInstanceClass": "db.t4g.micro",
                    "Endpoint": {"Address": "prod.abc.rds.aws", "Port": 5432},
                },
                {
                    "DBInstanceStatus": "available",
                    "Engine": "mysql",
                    "EngineVersion": "8.0",
                    "DBInstanceClass": "db.r6g.large",
                    "Endpoint": {"Address": "replica.abc.rds.aws", "Port": 3306},
                },
            ]
        },
    }

    with caplog.at_level(logging.WARNING):
        result = describe_rds_instance("prod-db")

    assert result["available"] is True
    assert result["engine"] == "postgres"  # first instance used
    assert "returned 2 instances" in caplog.text


@patch("app.tools.RDSEventsTool.execute_aws_sdk_call")
def test_describe_rds_events_success(mock_call) -> None:
    mock_call.return_value = {
        "success": True,
        "data": {
            "Events": [
                {
                    "Date": "2026-05-05T12:00:00Z",
                    "Message": "Multi-AZ instance failover started",
                    "EventCategories": ["failover"],
                    "SourceType": "db-instance",
                }
            ]
        },
    }

    result = describe_rds_events("prod-db", duration_minutes=120)
    assert result["available"] is True
    assert result["total_events"] == 1
    assert result["events"][0]["categories"] == ["failover"]


@patch("app.tools.RDSEventsTool.execute_aws_sdk_call")
def test_describe_rds_events_no_events(mock_call) -> None:
    mock_call.return_value = {"success": True, "data": {"Events": []}}

    result = describe_rds_events("prod-db")
    assert result["available"] is True
    assert result["total_events"] == 0
    assert result["events"] == []


@patch("app.tools.RDSEventsTool.execute_aws_sdk_call")
def test_describe_rds_events_failure(mock_call) -> None:
    mock_call.return_value = {"success": False, "error": "ThrottlingException"}

    result = describe_rds_events("prod-db")
    assert result["available"] is False
    assert result["error"] == "Failed to describe RDS events. Check server logs for details."
