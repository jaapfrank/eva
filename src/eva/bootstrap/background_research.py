"""Reuse native source research within one admitted digital task and its budget."""
from eva.application.conversation import ConversationConflict, ConversationExecutionLimitReached, ConversationModelUsage


class AccountedResearch:
    """Bind native research to the task journal; replay remains owned by tools."""

    def __init__(self, research, *, turn, journal, authorize, grant, input_bound, output_bound):
        self.research, self.turn, self.journal = research, turn, journal
        self.authorize, self.grant = authorize, grant
        if not input_bound or not output_bound:
            raise ValueError("research requires configured per-response bounds")
        self.input_bound, self.output_bound = input_bound, output_bound
        self.deadline_failure = None

    def native_research_timeout_seconds(self):
        return self.research.native_research_timeout_seconds()

    def research_request_payload(self, **kwargs):
        return self.research.research_request_payload(**kwargs)

    def research_configuration_sha256(self):
        return self.research.research_configuration_sha256()

    def research_current_options(self, **kwargs):
        self.authorize(self.turn)
        used = self.journal.model_usage(self.turn)
        # A tool is selected after a measured conversation request. Unknown
        # earlier usage cannot be interpreted as a fresh task allowance.
        if used.measurement != "measured":
            raise ConversationConflict("research requires measured prior task usage")
        # This reserves submitted-envelope capacity, not a provider hard cap:
        # hosted search may add input. Full actual usage is checked below.
        if (used.model_attempts >= self.grant.maximum_model_runs
                or self.grant.maximum_input_tokens - used.input_tokens < self.input_bound
                or self.grant.maximum_output_tokens - used.output_tokens < self.output_bound):
            raise ConversationExecutionLimitReached("remaining task budget cannot admit research")
        request_id = self.journal.begin_model_request(self.turn)
        try:
            artifact = self.research.research_current_options(**kwargs)
        except BaseException as error:
            usage = getattr(error, "model_usage", None)
            if not isinstance(usage, ConversationModelUsage):
                usage = ConversationModelUsage.unmeasured()
            self.journal.complete_model_request(self.turn, request_id, usage, outcome="failed")
            from eva.adapters.model.contracts import ConversationModelDeadlineExceeded
            if isinstance(error, ConversationModelDeadlineExceeded):
                self.deadline_failure = error
            raise
        # Complete once, before any subsequent validation or cancellation can fail.
        self.journal.complete_model_request(self.turn, request_id, artifact.usage)
        self.authorize(self.turn)
        if artifact.usage.measurement != "measured" or artifact.usage.model_attempts != 1:
            raise ConversationConflict("research usage is not one measured model request")
        actual = self.journal.model_usage(self.turn)
        if (actual.measurement != "measured"
                or actual.input_tokens > self.grant.maximum_input_tokens
                or actual.output_tokens > self.grant.maximum_output_tokens):
            raise ConversationExecutionLimitReached("research exhausted the task token grant")
        return artifact


def task_research_tools(engine, model, *, turn, journal, authorize, grant, profile):
    """Keep the existing durable receipt, cancellation and no-replay machinery."""
    from eva.adapters.model.research_tools import NativeResearchTool, OwnerResearchContinuationTool
    from eva.application.initiative_execution import require_initiative_context
    from eva.adapters.postgres.conversation_actions import PostgresConversationActions
    from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools

    research = AccountedResearch(model, turn=turn, journal=journal, authorize=authorize,
        grant=grant, input_bound=profile.maximum_response_input_tokens,
        output_bound=profile.maximum_response_output_tokens)
    # The same budget/journal serves both sources, but their disclosure policies
    # differ. Never present a native initiative as an ordinary owner request.
    adapter_type = (NativeResearchTool if require_initiative_context(turn) is not None
                    else OwnerResearchContinuationTool)
    adapter = adapter_type(research, PostgresConversationActions._validated_research_arguments)
    class TaskResearchTools(PostgresExternalConversationTools):
        # The generic orchestrator first persists its failed tool receipt. Surface
        # the original deadline before unknown usage can mask it as budget failure.
        def execute(self, *args, **kwargs):
            result = super().execute(*args, **kwargs)
            if research.deadline_failure is not None:
                raise research.deadline_failure
            return result

        def pending(self, *args, **kwargs):
            result = super().pending(*args, **kwargs)
            if research.deadline_failure is not None:
                raise research.deadline_failure
            return result

    return TaskResearchTools(engine, adapter, adapter, authorize_turn=authorize)
