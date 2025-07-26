import time
import yaml
from kubernetes import client, config
from kubernetes.client.rest import ApiException

DEPLOYMENT_NAME = "your-deployment-name"
NAMESPACE = "default"
ORIGINAL_RESOURCES_FILE = "original_resources.yaml"

# Define multiple variations to test
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

def wait_for_pods_ready(apps_v1):
    print("Waiting for pods to become ready...")
    start_time = time.time()
    while True:
        dep = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        ready_replicas = dep.status.ready_replicas or 0
        desired_replicas = dep.spec.replicas
        if ready_replicas == desired_replicas:
            break
        time.sleep(1)
    end_time = time.time()
    return end_time - start_time

def main():
    config.load_kube_config()
    apps_v1 = client.AppsV1Api()

    try:
        deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        original_resources = store_original_resources(deployment)

        timing_results = []

        for i, variation in enumerate(RESOURCE_VARIATIONS):
            print(f"\n🔁 Trial {i+1}: Applying resource variation {variation}")

            # Reload latest deployment object before patching
            deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)

            # Apply the variation to all containers
            resources_for_all = {
                name: variation for name in original_resources.keys()
            }
            patch_deployment_resources(apps_v1, deployment, resources_for_all)

            # Wait and record time
            ready_time = wait_for_pods_ready(apps_v1)
            print(f"✅ Pods ready in {ready_time:.2f} seconds for variation {i+1}")
            timing_results.append((variation, ready_time))

        # Restore original resources
        print("\n♻️ Restoring original resources...")
        deployment = apps_v1.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
        patch_deployment_resources(apps_v1, deployment, original_resources)
        print("✅ Original resources restored.")

        # Print summary
        print("\n📊 Resource Variation Timing Summary:")
        for i, (variation, t) in enumerate(timing_results, start=1):
            print(f"  Variation {i}: {variation} → {t:.2f} seconds")

    except ApiException as e:
        print(f"Kubernetes API error: {e}")
    except Exception as e:
        print(f"Unexpected error: {e}")

if __name__ == "__main__":
    main()
