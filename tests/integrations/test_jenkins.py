from unittest.mock import patch

from app.integrations._verification_adapters import result
from app.services.jenkins.client import JenkinsClient
from app.tools.JenkinsTool import get_jenkins_build_log


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
    from app.tools.JenkinsTool import (
    get_jenkins_build_log,
    list_jenkins_builds,
)


from app.tools.JenkinsTool import (
    get_jenkins_build_log,
    list_jenkins_builds,
)


@patch("app.services.jenkins.client.JenkinsClient.list_builds")
def test_list_jenkins_builds_tool(mock_list_builds):
    mock_list_builds.return_value = [
        {"number": 42, "url": "http://jenkins/job/deploy-api/42/"}
    ]

    result = list_jenkins_builds(
        job_name="deploy-api",
        jenkins_url="http://jenkins",
        jenkins_username="admin",
        jenkins_token="token",
    )

    assert result["source"] == "jenkins"
    assert len(result["builds"]) == 1


@patch("app.services.jenkins.client.JenkinsClient.get_build_log")
def test_get_jenkins_build_log_tool(mock_get_log):
    mock_get_log.return_value = "Build succeeded"

    result = get_jenkins_build_log(
        job_name="deploy-api",
        build_number=42,
        jenkins_url="http://jenkins",
        jenkins_username="admin",
        jenkins_token="token",
    )

    assert result["source"] == "jenkins"
    assert result["log"] == "Build succeeded"