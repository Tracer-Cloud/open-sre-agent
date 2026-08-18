"""Work the agent runs on a schedule or in the background.

``scheduler`` owns cron and agentic loop tasks and is hosted by the gateway
process; ``tasks`` holds the task types and registry it persists;
``background_investigations`` tracks investigations started from a surface and
finished after the turn returns.
"""
