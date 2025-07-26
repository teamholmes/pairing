import argparse
import json
import time
import yaml
from kubernetes import client, config
from copy import deepcopy


def get_deployment(api, name, namespace):
    return api.read_namespaced_deployment(name=name, namespace=namespace)


def update_resources(deployment, cpu_millicores, main_container, istio_cpu_millicores):
    new_spec = deepcopy(deployment)
    for container in new_spec.spec.template.spec.containers:
        name = container.name
        cpu = f"{istio_cpu_millicores}m" if name == "istio-proxy" else f"{cpu_millicores}m"
        container.resources.requests = {"cpu": cpu}
        container.resources.limits = {"cpu": cpu}
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
    parser.add_argument("--deployment", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--main-container", required=True)
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
    tested_cpus = [50, 100, 200, 300, 400, 500, 750, 1000]  # in millicores

    for cpu in tested_cpus:
        print(f"\n[TEST] Trying main: {cpu}m, istio: {int(cpu * 0.5)}m...")
        variation_dep = update_resources(
            deployment=original,
            cpu_millicores=cpu,
            main_container=args.main_container,
            istio_cpu_millicores=int(cpu * 0.5)
        )
        apply_deployment(apps_v1, variation_dep, args.namespace)

        duration = wait_for_pods_ready(core_v1, label_selector, args.namespace, replicas)
        result = {
            "main_cpu": cpu,
            "istio_cpu": int(cpu * 0.5),
            "ready_time_seconds": duration
        }
        print(f"Pods ready in {duration:.2f} seconds" if duration else "Timeout reached")
        results.append(result)

    print("\nRestoring original deployment...")
    apply_deployment(apps_v1, original, args.namespace)

    # Determine best CPU: lowest CPU with near-best time (within 5%)
    valid_results = [r for r in results if r["ready_time_seconds"] is not None]
    fastest_time = min((r["ready_time_seconds"] for r in valid_results), default=None)

    recommended = None
    if fastest_time is not None:
        tolerance = fastest_time * 1.05
        minimal_cpu = min(
            (r for r in valid_results if r["ready_time_seconds"] <= tolerance),
            key=lambda x: x["main_cpu"],
            default=None
        )
        recommended = minimal_cpu

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
        print("\n✅ Recommended CPU configuration:")
        print(json.dumps(recommended, indent=2))
    else:
        print("⚠️ No suitable configuration resulted in ready pods.")


if __name__ == "__main__":
    main()
