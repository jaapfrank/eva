"""Fresh private result reads without foreground action-journal slots."""
from eva.application.background_execution import authorize_background_dispatch
from eva.application.conversation import ConversationActionReceipt, ConversationConflict
from eva.adapters.postgres.delegated_work import DelegatedWorkNotFound
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions


class DelegatedWorkResultReader:
    action_names = ("get_delegated_work",)

    def __init__(self, service):
        self.service = service

    @staticmethod
    def conversation_tools():
        return tuple(tool for tool in PostgresDelegatedWorkActions.conversation_tools()
                     if tool.name == "get_delegated_work")

    def available_for(self, turn):
        return self.service.available_for(turn)

    def active_context(self, turn):
        return ()

    def pending(self, turn, *, step_index=0):
        # Reads have no effect to replay and must recheck current retention.
        self.service.validate_authority(turn)
        return None

    def execute(self, turn, call, *, step_index=0):
        if (call.name != "get_delegated_work" or set(call.arguments) != {"work_id"}
                or not isinstance(call.arguments["work_id"], str)
                or not 1 <= len(call.arguments["work_id"]) <= 240):
            raise ConversationConflict("background result reader requires one work ID")
        self.service.validate_authority(turn)
        authorize_background_dispatch(turn)
        identifier = call.arguments["work_id"]
        try:
            before = self.service.store.load(identifier, **self.service._owner)
            if before.retention_until <= self.service.now():
                raise DelegatedWorkNotFound("prior work retention expired")
            result = self.service.execute("get", turn, work_id=identifier,
                                          request_key=call.call_id)
            after = self.service.store.load(identifier, **self.service._owner)
            if after.retention_until <= self.service.now():
                raise DelegatedWorkNotFound("prior work retention expired")
        except DelegatedWorkNotFound:
            result = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": "get",
                      "status": "not_found", "work": None}
        self.service.validate_authority(turn)
        return ConversationActionReceipt(call, {"status": "verified", "operation_result": result})
