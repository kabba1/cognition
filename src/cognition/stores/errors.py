"""Store conflicts that callers can handle without partial writes."""


class RevisionConflict(ValueError):
    """An expected durable revision is no longer current."""
