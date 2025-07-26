import argparse
import json
import time
import yaml
from kubernetes import client, config
from copy import deepcopy


RESOURCE_VARIATIONS = [
    {
        "main": {"requests": {"cpu": "100m"}, "limits": {"cpu": "200m"}},
        "istio-proxy": {"requests": {"cpu": "50m"}, "limits": {"cpu": "100m"}}
    },
    {
        "main": {"requests": {"cpu": "250m"}, "limits": {"cpu": "500m"}},
        "istio-proxy": {"requests": {"cpu": "100m"}, "limits": {"cpu": "200m"}}
    },
    {
        "main": {"requests": {"cpu": "500m"}, "limits": {"cpu": "1"}},
        "istio-proxy": {"requests": {"cpu": "200m"}, "limits": {"cpu": "400m"}}
    }
]


def get_deployment(api, name, namespace):
    return api.read_namespaced_deployment(name=name, namespace=namespace)


def update_resources(deployment, variation, main_container_name):
    new_spec = deepcopy(deployment)
    for container in new_spec.spec.template.spec.containers:
        name = container.name
        if name == "istio-proxy" and "istio-proxy" in variation:
            container.resources.requests = variation["istio-proxy"]["requests"]
            container.resources.limits = variation["istio-proxy"]["limits"]
        elif name == main_container_name and "main" in variation:
            container.resources.requests = variation["main"]["requests"]
            container.resources.limits = variation["main"]["limits"]
    return new_spec


def apply_deployment(api, deployment, namespace):
    api.replace_namespaced_deployment(
        name=deployment.metadata.name,
        namespace=namespace,
        body=deployment
    )


def wait_for_pods_ready(api, label_selector, namespace, replicas, timeout=300):
    start_time = time.time()
    while time.time() - start_time < timeout:
        pods = api.list_namespaced_pod(namespace, label_selector=label_selector).items
        ready_pods = [pod for pod in pods if all(c.ready for c in pod.status.container_statuses or [])]
        if len(ready_pods) >= replicas:
            return time.time() - start_time
        time.sleep(2)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment", required=True, help="Name of the deployment")
    parser.add_argument("--namespace", required=True, help="Kubernetes namespace")
    parser.add_argument("--main-container", required=True, help="Name of the main application container")
    parser.add_argument("--output", help="Optional JSON output file")
    args = parser.parse_args()

    config.load_kube_config()
    apps_v1 = client.AppsV1Api()
    core_v1 = client.CoreV1Api()

    original = get_deployment(apps_v1, args.deployment, args.namespace)
    original_yaml = yaml.safe_dump(original.to_dict())

    label_selector = ",".join([f"{k}={v}" for k, v in original.spec.selector.match_labels.items()])
    replicas = original.spec.replicas

    results = []
    for i, variation in enumerate(RESOURCE_VARIATIONS, start=1):
        print(f"\n[TEST {i}] Applying resource variation...")
        new_dep = update_resources(original, variation, args.main_container)
        apply_deployment(apps_v1, new_dep, args.namespace)

        print("Waiting for pods to become ready...")
        duration = wait_for_pods_ready(core_v1, label_selector, args.namespace, replicas)

        result = {
            "variation": variation,
            "ready_time_seconds": duration
        }
        print(f"Pods ready in {duration:.2f} seconds" if duration else "Timeout reached")
        results.append(result)

    print("\nRestoring original deployment...")
    apply_deployment(apps_v1, original, args.namespace)

    recommended = min((r for r in results if r["ready_time_seconds"]), key=lambda x: x["ready_time_seconds"], default=None)

    output = {
        "original": yaml.safe_load(original_yaml),
        "results": results,
        "recommended": recommended
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output, f, indent=2, default=str)
        print(f"Results written to {args.output}")

    if recommended:
        print("\nBest variation (fastest):")
        print(json.dumps(recommended, indent=2))
    else:
        print("No variation resulted in ready pods within timeout.")


if __name__ == "__main__":
    main()
