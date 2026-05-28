"""Synthetic suite for the SigNoz integration.

Exercises the four SigNoz tools (logs, traces, services, metrics) end-to-end
against realistic mocked SigNoz v5 query_range responses. The fixtures mirror
the actual envelope shape returned by SigNoz Cloud (outer ``{status, data}``
wrapper around the ``QueryRangeResponse`` struct from
``pkg/types/querybuildertypes/querybuildertypesv5/resp.go`` in the SigNoz
source). Catches client-to-tool wiring bugs that pure unit tests miss
because they mock at the client boundary.
"""
