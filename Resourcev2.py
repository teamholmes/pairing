import time
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_NAME = "your-deployment-name"
NAMESPACE = "default"
ORIGINAL_RESOURCES_FILE = "original_resources.yaml"

# Define test variations
RESOURCE_VARIATIONS = [
    {"requests": {"cpu": "200m", "memory": "128Mi"}, "limits": {"cpu": "400m", "memory": "256Mi"}},
    {"requests": {"cpu": "300m", "memory": "192Mi"}, "limits": {"cpu": "600m", "memory": "384Mi"}},
    {"requests": {"cpu": "400m", "memory": "256Mi"}, "limits": {"cpu": "800m", "memory": "512Mi"}},
]

def store_original_resources(deployment):
    original_resources = {}
    for container in deployment.spec.template.spec.containers:
        original_resources[container.name] = container.resources.to_dict()
    with open(ORIGINAL_RESOURCES_FILE, "w") as f:
        yaml.dump(original_resources, f, default_flow_style=False)
    print(f"Original resources saved to {ORIGINAL_RESOURCES_FILE}")
    return original_resources

def patch_deployment_resources(apps_v1, deployment, resources_dict):
    for i, container in enumerate(deployment.spec.template.spec.containers):
        container_name = container.name
        resource_conf = resources_dict.get(container_name, {})
        deployment.spec.template.spec.containers[i].resources = client.V1ResourceRequirements(
            requests=resource_conf.get("requests"),
            limits=resource_conf.get("limits")
        )
    apps_v1.patch_namespaced_deployment(
        name=DEPLOYMENT_NAME,
        namespace=NAMESPACE,
        body=deployment
    )

def get_latest_replicaset(apps_v1, deployment):
    rs_list = apps_v1.list_namespaced_replica_set(namespace=NAMESPACE)
    deployment_uid = deployment.metadata.uid

    owned_rs = [
        rs for rs in rs_list.items
        if rs.metadata.owner_references and any(ref.uid == deployment_uid for ref in rs.metadata.owner_references)
    ]

    if not owned_rs:
        raise RuntimeError("No ReplicaSets found for the deployment.")

    # Return the one with the most recent creationTimestamp
    latest_rs = sorted(owned_rs, key=lambda rs: rs.metadata.creation_timestamp, reverse=True)[0]
    return latest_rs

def wait_for_replicaset_pods_ready(apps_v1, core_v1, deployment):
    print("Waiting for pods of latest ReplicaSet to become ready...")
    latest_rs = get_latest_replicaset(apps_v1, deployment)
    selector = latest_rs.spec.selector.match_labels
    label_selector = ",".join([f"{k}={v}" for k, v in selector.items()])

    desired_replicas = latest_rs.spec.replicas
    start_time = time.time()

    while True:
        pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=label_selector).items
        ready_pods = [p for p in pods if all(
            c.ready for c in p.status.container_statuses or []
        ) and p.status.phase == "Running"]

        if len(ready_pods) >= desired_replicas:
            break
        time.sleep(1)

    end_time = time.time()
    return end_time - start_time

def main():
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()

    try:
        # Step 1: Store original resources
        deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        original_resources = store_original_resources(deployment)

        # Step 2: Add original as last variation
        container_names = list(original_resources.keys())
        extended_variations = RESOURCE_VARIATIONS.copy()
        extended_variations.append(next(iter(original_resources.values())))  # assumes uniform original config

        timing_results = []

        for i, variation in enumerate(extended_variations):
            is_original = (i == len(extended_variations) - 1)
            label = "original" if is_original else f"variation {i+1}"
            print(f"\n🔁 Applying {label}: {variation}")

            # Reload latest deployment
            deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)

            # Build per-container config
            resource_config = {
                name: (original_resources[name] if is_original else variation)
                for name in container_names
            }

            patch_deployment_resources(apps_v1, deployment, resource_config)

            # Wait for pods managed by latest ReplicaSet
            ready_time = wait_for_replicaset_pods_ready(apps_v1, core_v1, deployment)
            print(f"✅ Pods ready in {ready_time:.2f} seconds for {label}")
            timing_results.append((label, variation, ready_time))

        # Step 3: Restore original one final time
        print("\n♻️ Restoring original resources...")
        deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        patch_deployment_resources(apps_v1, deployment, original_resources)
        print("✅ Original resources restored.")

        # Step 4: Print summary
        print("\n📊 Resource Variation Timing Summary:")
        for label, variation, t in timing_results:
            print(f"  {label:<12} → {t:.2f} sec  |  {variation}")

    except ApiException as e:
        print(f"Kubernetes API error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
