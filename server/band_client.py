"""BAND owner's integration seam; implement real remote-agent routing here.
Return validated models; failures must propagate, never silently substitute fixtures.
"""
from server.settings import DEMO_MODE
from server.agents.opportunity_agent import demo_opportunity
from server.agents.reflection_agent import demo_reflection

async def opportunity(state):
    if not DEMO_MODE:
        raise RuntimeError('Live BAND opportunity adapter is not implemented')
    return await demo_opportunity(state)

async def reflection(mission, recording_id, transcript):
    if not DEMO_MODE:
        raise RuntimeError('Live BAND reflection adapter is not implemented')
    return await demo_reflection(mission, recording_id, transcript)
