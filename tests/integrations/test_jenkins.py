from unittest.mock import patch

from app.services.jenkins.client import JenkinsClient


@patch("app.services.jenkins.client.httpx.get")
def test_list_jobs(mock_get):
    mock_get.return_value.json.return_value = {
        "jobs": [
            {
                "name": "deploy-api",
                "url": "http://jenkins/job/deploy-api/",
                "color": "blue",
            }
        ]
    }
    mock_get.return_value.raise_for_status.return_value = None

    client = JenkinsClient(
        base_url="http://jenkins",
        username="admin",
        token="token",
    )

    jobs = client.list_jobs()

    assert len(jobs) == 1
    assert jobs[0]["name"] == "deploy-api"


@patch("app.services.jenkins.client.httpx.get")
def test_list_builds(mock_get):
    mock_get.return_value.json.return_value = {
        "builds": [
            {
                "number": 42,
                "url": "http://jenkins/job/deploy-api/42/",
            }
        ]
    }
    mock_get.return_value.raise_for_status.return_value = None

    client = JenkinsClient(
        base_url="http://jenkins",
        username="admin",
        token="token",
    )

    builds = client.list_builds("deploy-api")

    assert len(builds) == 1
    assert builds[0]["number"] == 42
@patch("app.services.jenkins.client.httpx.get")
def test_get_build_log(mock_get):
    mock_get.return_value.text = "Build succeeded"
    mock_get.return_value.raise_for_status.return_value = None

    client = JenkinsClient(
        base_url="http://jenkins",
        username="admin",
        token="token",
    )

    log = client.get_build_log("deploy-api", 42)

    assert log == "Build succeeded"