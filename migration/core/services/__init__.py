"""Service-layer helpers for the Migration agent-app.

Services orchestrate multi-doc operations on top of the Integration*
doctypes. Endpoint files (api.py) call into these helpers and translate
results into the `{ok, message, data}` envelope.
"""
