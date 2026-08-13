/** What the Deploy gallery is allowed to claim about its five templates.
 *
 * The registry is static data, so the interesting failures are not logic
 * errors — they are a template whose yaml was truncated in an edit, a chip
 * carrying a tag the router has never emitted, or a "1 task" line under a
 * config that expands to six. Each of those renders perfectly and is wrong,
 * which is exactly the class of thing a test has to hold.
 */

import { describe, expect, it } from "vitest";

import {
  DEPLOY_TEMPLATES,
  TEMPLATE_SUBMIT_GAP,
  WORKLOAD_TAGS,
  templateById,
  templateMeta,
  workloadKind,
} from "./templates";

describe("the registry", () => {
  it("is the five demo-suite workloads, by their directory names", () => {
    // The ids are the directories in the public repo, and the gallery is
    // deliberately not a place things get added casually: a sixth card
    // means a sixth example that somebody has actually run.
    expect(DEPLOY_TEMPLATES.map((t) => t.id)).toEqual([
      "train",
      "hpo",
      "federated",
      "evaluate",
      "gpu-train",
    ]);
  });

  it("gives every template a name, a line and a provenance path", () => {
    for (const t of DEPLOY_TEMPLATES) {
      expect(t.name.trim().length).toBeGreaterThan(0);
      expect(t.demonstrates.trim().length).toBeGreaterThan(0);
      expect(t.source).toBe(`examples/demo-suite/${t.id}/flashml.yaml`);
    }
  });

  it("carries real yaml, not a placeholder", () => {
    for (const t of DEPLOY_TEMPLATES) {
      // A `flashml.yaml` this product will accept must name what to run and
      // what to run it on. Both keys being present in every template is the
      // cheapest evidence that the copy is the whole file rather than a
      // truncation that still parses.
      expect(t.yaml).toContain("entrypoint:");
      expect(t.yaml).toContain("image:");
      expect(t.yaml).toContain(`name: demo`);
      expect(t.yaml.trim().length).toBeGreaterThan(500);
      // Copied verbatim, comments and all — see the module docblock on why
      // the comments are the point rather than noise.
      expect(t.yaml).toContain("#");
    }
  });

  it("gives each template its own yaml", () => {
    const yamls = new Set(DEPLOY_TEMPLATES.map((t) => t.yaml));
    expect(yamls.size).toBe(DEPLOY_TEMPLATES.length);
  });
});

describe("workload tags", () => {
  it("only tags a card with a kind the router can answer with", () => {
    for (const t of DEPLOY_TEMPLATES) {
      expect(WORKLOAD_TAGS).toContain(t.tag);
    }
  });

  it("tags the five as the demo suite's own README does", () => {
    // Not taste: these five verdicts are quoted in
    // `examples/demo-suite/README.md`, each with the classifier's evidence
    // sentence beside it. `train` is COMMAND and not TRAINING on purpose —
    // TRAINING requires a GPU request, and an unclaimed kind beats a
    // confident wrong one.
    expect(DEPLOY_TEMPLATES.map((t) => `${t.id}:${t.tag}`)).toEqual([
      "train:COMMAND",
      "hpo:HPO",
      "federated:FEDERATED",
      "evaluate:EVALUATION",
      "gpu-train:TRAINING",
    ]);
  });

  it("maps a tag to the lowercase value the API sends", () => {
    expect(workloadKind("EVALUATION")).toBe("evaluation");
    for (const tag of WORKLOAD_TAGS) {
      expect(workloadKind(tag)).toBe(tag.toLowerCase());
    }
  });
});

describe("the meta line", () => {
  it("counts tasks, and pluralises", () => {
    const train = templateById("train");
    const hpo = templateById("hpo");
    expect(templateMeta(train!)).toBe("1 task · CPU");
    expect(templateMeta(hpo!)).toBe("6 tasks · CPU");
  });

  it("says how the sweep's six tasks arise", () => {
    // 3 lr values x 2 hidden values. If the yaml's sweep block is edited and
    // this count is not, the card claims a task count the config does not
    // produce — the one number on the card a reader would plan around.
    const hpo = templateById("hpo")!;
    expect(hpo.yaml).toContain("lr: [0.1, 0.3, 1.0]");
    expect(hpo.yaml).toContain("hidden: [4, 16]");
    expect(hpo.tasks).toBe(3 * 2);
  });

  it("never invents a task count for the federated job", () => {
    // A federated round is cut into as many chunks as the machines that
    // show up can finish, so the count is a property of the fleet at run
    // time. `null` plus a note, never a plausible integer.
    const federated = templateById("federated")!;
    expect(federated.tasks).toBeNull();
    expect(federated.tasksNote).not.toBeNull();
    expect(templateMeta(federated)).toBe(
      "4 rounds · one shard per machine · CPU"
    );
  });

  it("marks exactly one template as GPU work", () => {
    const gpu = DEPLOY_TEMPLATES.filter((t) => t.hardware === "GPU");
    expect(gpu.map((t) => t.id)).toEqual(["gpu-train"]);
    // And it is GPU because the config says so, not because a field was set.
    expect(gpu[0].yaml).toContain("gpus: 1");
    for (const t of DEPLOY_TEMPLATES) {
      if (t.hardware === "CPU") expect(t.yaml).not.toContain("gpus:");
    }
  });
});

describe("lookup", () => {
  it("resolves an id, and refuses anything else", () => {
    expect(templateById("hpo")?.id).toBe("hpo");
    // The submit page reads this out of a query string, so the argument is
    // whatever somebody typed.
    expect(templateById("nope")).toBeUndefined();
    expect(templateById("")).toBeUndefined();
    expect(templateById(null)).toBeUndefined();
  });
});

describe("the submit gap", () => {
  it("is stated once and says what checking does not do", () => {
    expect(TEMPLATE_SUBMIT_GAP).toMatch(/creates nothing/);
  });
});
