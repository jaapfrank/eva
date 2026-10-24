"""Give an isolated extension's proposal to the existing household decision.

An extension supplies task data, never a decision or delivery authority.
"""
from dataclasses import replace
from copy import deepcopy
from datetime import UTC, datetime

from eva.application.free_will import FreeWillDecision, HouseholdProcedureLineage
from eva.contracts.canonical_json import canonical_sha256


class HouseholdProcedureContext(dict):
    """Ordinary model context with separately retained host-only provenance."""
    def __init__(self, context, lineage):
        super().__init__(context)
        if not isinstance(lineage, HouseholdProcedureLineage):
            raise ValueError('household procedure context requires host lineage')
        self.lineage = lineage


class HouseholdProvisionInactive(RuntimeError):
    """Known lifecycle denial before dispatch, not an uncertain external effect."""


class HouseholdProcedureModel:
    def __init__(self, model, proposal, *, authorize, clock=None):
        self.model, self.proposal, self.authorize = model, proposal, authorize
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def __getattr__(self, name):
        return getattr(self.model, name)

    def evaluate_sensory_event(self, event, *args, **kwargs):
        self.authorize()
        context = self.proposal(event, now=self.clock())
        if context is None:
            return self.model.evaluate_sensory_event(event, *args, **kwargs)
        expires = context['expires_at_epoch']
        if self.clock().timestamp() >= expires:
            return FreeWillDecision('silence', 'Het voorstel is niet meer actueel.', 0,
                                    'eva:expired-procedure-proposal')
        self.authorize()
        result = self.model.evaluate_sensory_event(event, *args,
            household_proposal_context=deepcopy(dict(context)), **kwargs)
        try:
            self.authorize()
            # Recheck source/rights after the model; never execute the module twice.
            self.proposal.validate_current(context)
        except HouseholdProvisionInactive:
            return FreeWillDecision('silence', 'De voorziening is niet actief.', 0,
                'eva:inactive-household-provision', usage=result.usage)
        except Exception as error:
            error.model_usage = result.usage
            error.validation_failure_stage = "sensory_decision"
            error.validation_failure_code = "household_procedure_revoked_after_model"
            raise
        if self.clock().timestamp() >= expires:
            return FreeWillDecision('silence', 'Het voorstel is tijdens de afweging verlopen.', 0,
                'eva:expired-procedure-proposal', usage=result.usage)
        return replace(result, expression_expires_at_epoch=expires,
                       source_context_sha256=canonical_sha256(context),
                       expression_guard_sha256=context["binding_sha256"],
                       household_procedure_lineage=(context.lineage
                           if isinstance(context, HouseholdProcedureContext) else None))
