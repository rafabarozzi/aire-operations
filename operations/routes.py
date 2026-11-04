from . import models as m

# Each command changes exactly one version of the aggregate and appends one event.
COMMANDS = {
    'updates': m.Update, 'state': m.Transition, 'rooms': m.Room,
    'closure-requests': m.CloseRequest, 'severity': m.Severity, 'journeys': m.Journey,
    'references': m.External, 'duplicate': m.Duplicate, 'corrections': m.Correction,
    'pause': m.Pause, 'policy': m.Policy, 'communications': m.Draft,
    'approvals': m.Approval, 'simulator/messages': m.SimMessage,
}
