# liu bai (留白): test helpers. Provenance watermark: liu bai.\n"""Shared test helpers."""
from four_layer_memory import approval
from four_layer_memory.store import Entry


def add_a(store, text, source='m1', source_event_id='evt-1', approver='test', **kwargs):
    """Write a layer-A entry through the host approval channel."""
    kwargs.setdefault('authority', 'user-explicit')
    kwargs.setdefault('evidence', 'quote')
    entry = Entry('A', text, source, source_event_id=source_event_id,
                  approved=True, **kwargs)
    record = approval.mint(store.root, entry_id=entry.entry_id, text=entry.text,
                           source_event_id=entry.source_event_id, approver=approver)
    return store.add(entry, approval_record=record)
