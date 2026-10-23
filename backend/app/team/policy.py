"""Recorded policy decisions for team tools; execution rechecks membership."""
from dataclasses import dataclass


# Origins that no person is watching: they may not create agents or rooms.
UNATTENDED = ('outside', 'webhook', 'routine')
# Least to most trusted-away; a room turn takes the strictest origin it read.
_RANK = ('person', 'routine', 'webhook', 'outside')


def strictest(origins):
    known = [o for o in origins if o in _RANK]
    return max(known, key=_RANK.index) if known else 'person'


@dataclass(frozen=True)
class TurnTeam:
    team_id: int
    agent_id: int
    is_lead: bool
    lead_auto: bool
    origin: str = 'person'
    depth: int = 0
    delegation_enabled: bool = True


def automatic(tool, mode, team, arguments):
    if not tool.startswith('mcp__team__'):
        return None
    if team is None:
        return 'denied', 'no_team'
    name = tool.removeprefix('mcp__team__')
    if name in ('list_agents', 'list_rooms', 'list_team_setup', 'list_threads'):
        return 'allowed', 'team_read'
    if name == 'ask_agent':
        if not team.delegation_enabled:
            return 'denied', 'delegation_disabled'
        if arguments.get('mode') == 'consult':
            return 'allowed', 'team_consult'
        if mode == 'plan':
            return 'denied', 'plan_mode'
        if team.origin in ('outside', 'webhook'):
            return None
    elif name in ('create_agent', 'create_room', 'propose_team_setup', 'propose_bot_deletion', 'close_thread'):
        if mode == 'plan':
            return 'denied', 'plan_mode'
        if team.origin in UNATTENDED:
            return 'denied', 'outside_origin'
        if not team.is_lead and name != 'close_thread':
            return 'denied', 'lead_only'
        if name == 'propose_bot_deletion':
            return None  # Removing an agent is always the person's card, never automatic.
        if name == 'close_thread':
            return 'allowed', 'team_housekeeping'
    elif name == 'say_in_room':
        return 'allowed', 'team_room_post'
    else:
        return 'denied', 'unknown_team_tool'
    if team.is_lead and team.lead_auto:
        return 'allowed', 'team_policy'
    return None
