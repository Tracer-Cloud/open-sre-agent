"""The Kubernetes action and gather fragments are one text in two files.

They are byte-identical by design: the gather stage plans an investigation and
the action stage answers a chat turn, but the reasoning failure they exist to
prevent — concluding "nothing is wrong" from one namespace of one cluster, or
looking for a workload's telemetry only in the project the alert named — is the
same on both paths.

Nothing in the type system couples them. Editing one and forgetting the other
leaves the two surfaces disagreeing, and the symptom is a chat answer that is
subtly worse than the investigation's for reasons no one can see in a diff.
"""

from __future__ import annotations

from integrations.kubernetes.action_prompt import kubernetes_action_prompt_fragment
from integrations.kubernetes.gather_prompt import kubernetes_gather_prompt_fragment


def test_kubernetes_action_and_gather_fragments_stay_identical() -> None:
    assert kubernetes_action_prompt_fragment() == kubernetes_gather_prompt_fragment()


def test_the_fragment_reads_as_prose_not_run_together_sentences() -> None:
    """A missing space between two adjacent literals fuses two sentences.

    ``"...were checked." "A cloud project..."`` renders as ``checked.A cloud``.
    It is invisible in the source, survives review, and degrades the prompt.
    """
    fragment = kubernetes_action_prompt_fragment()

    for index, char in enumerate(fragment[:-1]):
        if char == "." and fragment[index + 1].isupper():
            raise AssertionError(
                f"missing space after a period: ...{fragment[index - 40 : index + 20]}..."
            )


def test_the_fragment_names_the_three_recoveries_it_exists_for() -> None:
    """Each clause maps to a live failure this slice was opened to fix."""
    fragment = kubernetes_action_prompt_fragment()

    # An Argo Rollout owns the pods, so every apps/v1 lister correctly returns
    # nothing and the agent reports "no such workload".
    assert "kubernetes_list_workloads" in fragment
    # The alert named an observability project; the workload runs elsewhere.
    assert "monitoring scope, not a runtime" in fragment
    assert "project='*'" in fragment
    # The last resort before a false negative.
    assert "kubernetes_search_fleet" in fragment
    assert "before saying it does not exist" in fragment
