"""Compose finite repair handoffs inside the existing component host service."""
from pathlib import Path


def configured_runtime_repairs(config,*,settings,programming,applications,runtime_config):
    if (not isinstance(config,dict) or not {'receipt_root','target_root','publisher'}<=set(config)
            or set(config)-{'receipt_root','target_root','publisher','poll_seconds'}
            or programming is None or applications is None
            or runtime_config.get('runtime_repair_enabled') is not True):
        raise ValueError('runtime repair requires existing programming/application routes and owner grant')
    from eva.operations.component_application_runtime import ComponentApplicationRuntime
    from eva.operations.component_release_publisher import ComponentReleasePublisher
    from eva.operations.runtime_repair_artifacts import RuntimeRepairArtifacts
    from eva.operations.runtime_repair_target import RuntimeRepairTargets
    from eva.operations.runtime_repair_evaluation import RuntimeRepairEvaluator
    from eva.operations.runtime_repair_development import RuntimeRepairDevelopment
    from eva.operations.runtime_repair_publisher import current_repair_installation,read_image_source
    runtime=ComponentApplicationRuntime(runtime_config);publisher=ComponentReleasePublisher(config['publisher'])
    root=Path(config['receipt_root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
    holder={}
    targets=RuntimeRepairTargets(config['target_root'],
        binding=lambda incident:runtime.repair_call('binding',incident_id=incident),
        installation=lambda:current_repair_installation(publisher,root)[1],
        source=lambda image,path:read_image_source(publisher,image,path,root),
        criteria=lambda scope:holder['controller'].criteria(scope))
    controller=RuntimeRepairDevelopment(root,runtime=runtime,targets=targets,
        evaluator=RuntimeRepairEvaluator(targets,settings),artifacts=RuntimeRepairArtifacts(programming),
        publisher=publisher,applications=applications,scope_sha256=runtime_config['scope_sha256'],
        poll_seconds=config.get('poll_seconds',2),start=False)
    holder['controller']=controller
    # Reuse this service's existing lifecycle; no new always-on Eva process or model.
    import threading
    controller.thread=threading.Thread(target=controller._run,name='eva-component-repair-handoff',daemon=True)
    controller.thread.start()
    return controller
