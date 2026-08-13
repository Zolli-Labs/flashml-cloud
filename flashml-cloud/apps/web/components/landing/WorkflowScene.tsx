"use client";

import { AnimatePresence, motion } from "motion/react";
import type { CSSProperties } from "react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { DURATIONS, EASINGS, TRAVEL, seconds } from "@/lib/motion/timing";
import type {
  WorkflowEvent,
  WorkflowStepId,
} from "@/lib/landing/workflow";

type SceneTone = "neutral" | "orange" | "green" | "warning";

interface SceneObject {
  id: string;
  label: string;
  detail?: string;
  tone: SceneTone;
  x: number;
  y: number;
}

interface ScenePath {
  id: string;
  d: string;
  tone: SceneTone;
}

export interface WorkflowSceneDescriptor {
  id: WorkflowStepId;
  title: string;
  question: string;
  outcome: string;
  objects: readonly SceneObject[];
  paths: readonly ScenePath[];
  events: readonly WorkflowEvent[];
}

export const WORKFLOW_SCENES = [
  {
    id: "connect",
    title: "Capacity joins",
    question: "Which machines can enter the fleet?",
    outcome: "Four sources, one connection point",
    objects: [
      { id: "everyday", label: "Everyday", detail: "laptop", tone: "neutral", x: 15, y: 25 },
      { id: "owned", label: "Owned GPU", detail: "team fleet", tone: "neutral", x: 15, y: 74 },
      { id: "rented", label: "Rented", detail: "CPU / GPU", tone: "neutral", x: 85, y: 25 },
      { id: "external", label: "Cloud / HPC", detail: "external", tone: "neutral", x: 85, y: 74 },
      { id: "flashnode", label: "Zolli node", detail: "connected", tone: "orange", x: 50, y: 50 },
    ],
    paths: [
      { id: "connect-left", d: "M96 76 C190 76 205 180 320 180 M96 266 C190 266 205 180 320 180", tone: "neutral" },
      { id: "connect-right", d: "M544 76 C450 76 435 180 320 180 M544 266 C450 266 435 180 320 180", tone: "neutral" },
    ],
    events: [],
  },
  {
    id: "register",
    title: "Capacity becomes visible",
    question: "What does the control plane learn?",
    outcome: "A node record the scheduler can use",
    objects: [
      { id: "node", label: "Node", detail: "connected", tone: "neutral", x: 14, y: 50 },
      { id: "capability", label: "Capacity", detail: "CPU / GPU", tone: "neutral", x: 39, y: 28 },
      { id: "health", label: "Health", detail: "heartbeat", tone: "green", x: 39, y: 72 },
      { id: "registry", label: "Control plane", detail: "node registered", tone: "orange", x: 78, y: 50 },
    ],
    paths: [
      { id: "register-path", d: "M90 180 C180 180 175 102 250 102 M90 180 C180 180 175 258 250 258 M300 102 C380 102 380 180 500 180 M300 258 C380 258 380 180 500 180", tone: "orange" },
    ],
    events: [],
  },
  {
    id: "submit",
    title: "One job enters",
    question: "What work reaches the control plane?",
    outcome: "A bounded job is ready to lease",
    objects: [
      { id: "repository", label: "Repository", detail: "code", tone: "neutral", x: 16, y: 35 },
      { id: "definition", label: "Job spec", detail: "workload", tone: "neutral", x: 16, y: 68 },
      { id: "job", label: "Control plane", detail: "job ready", tone: "orange", x: 72, y: 50 },
    ],
    paths: [
      { id: "submit-path", d: "M105 126 C230 126 245 180 430 180 M105 245 C230 245 245 180 430 180", tone: "orange" },
    ],
    events: ["LEASE_CLAIMED"],
  },
  {
    id: "parallel",
    title: "Tasks fan out",
    question: "How does one job run in parallel?",
    outcome: "Bounded work reaches four workers",
    objects: [
      { id: "scheduler", label: "Scheduler", detail: "lease", tone: "orange", x: 50, y: 50 },
      { id: "task-a", label: "Task A", detail: "leased", tone: "green", x: 15, y: 25 },
      { id: "task-b", label: "Task B", detail: "leased", tone: "green", x: 15, y: 75 },
      { id: "task-c", label: "Task C", detail: "leased", tone: "green", x: 85, y: 25 },
      { id: "task-d", label: "Task D", detail: "leased", tone: "green", x: 85, y: 75 },
    ],
    paths: [
      { id: "parallel-left", d: "M320 180 C235 180 220 90 100 90 M320 180 C235 180 220 270 100 270", tone: "orange" },
      { id: "parallel-right", d: "M320 180 C405 180 420 90 540 90 M320 180 C405 180 420 270 540 270", tone: "orange" },
    ],
    events: [],
  },
  {
    id: "checkpoint",
    title: "Progress is recorded",
    question: "What survives while work continues?",
    outcome: "A verified checkpoint is available",
    objects: [
      { id: "worker", label: "Worker", detail: "running", tone: "neutral", x: 13, y: 50 },
      { id: "progress", label: "Progress", detail: "step 30", tone: "green", x: 38, y: 50 },
      { id: "manifest", label: "Manifest", detail: "hash recorded", tone: "orange", x: 63, y: 50 },
      { id: "checkpoint-store", label: "Checkpoint", detail: "verified", tone: "green", x: 88, y: 50 },
    ],
    paths: [
      { id: "checkpoint-path", d: "M82 180 H558", tone: "green" },
    ],
    events: ["CHECKPOINT_MANIFEST_COMMITTED"],
  },
  {
    id: "recover",
    title: "Interrupted work resumes",
    question: "What happens when a node disappears?",
    outcome: "The task restarts from recorded progress",
    objects: [
      { id: "lost-node", label: "Node", detail: "heartbeat lost", tone: "warning", x: 15, y: 32 },
      { id: "saved-checkpoint", label: "Checkpoint", detail: "verified", tone: "green", x: 15, y: 72 },
      { id: "requeued-task", label: "Task", detail: "requeued", tone: "orange", x: 52, y: 52 },
      { id: "replacement-node", label: "Next worker", detail: "resumed", tone: "green", x: 86, y: 52 },
    ],
    paths: [
      { id: "failure-path", d: "M100 115 C210 115 210 188 335 188", tone: "warning" },
      { id: "resume-path", d: "M100 258 C210 258 210 188 335 188 C430 188 450 188 550 188", tone: "green" },
    ],
    events: ["NODE_HEARTBEAT_LOST", "TASK_REQUEUED"],
  },
  {
    id: "accept",
    title: "One result is retained",
    question: "Which completion becomes final?",
    outcome: "The accepted task commit closes the path",
    objects: [
      { id: "candidate", label: "Candidate", detail: "task commit", tone: "neutral", x: 15, y: 50 },
      { id: "verification", label: "Verify", detail: "commit", tone: "orange", x: 50, y: 50 },
      { id: "accepted", label: "Accepted", detail: "one result", tone: "green", x: 85, y: 50 },
    ],
    paths: [
      { id: "accept-path", d: "M95 180 H545", tone: "green" },
    ],
    events: ["TASK_COMMIT_ACCEPTED"],
  },
] as const satisfies readonly WorkflowSceneDescriptor[];

type SceneObjectStyle = CSSProperties & {
  "--scene-x": string;
  "--scene-y": string;
};

export function WorkflowScene({
  step,
  compact = false,
}: {
  step: WorkflowStepId;
  compact?: boolean;
}) {
  const { reduced } = useLandingMotion();
  const scene = WORKFLOW_SCENES.find(({ id }) => id === step) ?? WORKFLOW_SCENES[0];

  return (
    <AnimatePresence mode="wait" initial={false}>
      <motion.article
        key={scene.id}
        data-workflow-scene={scene.id}
        data-compact={compact ? "true" : "false"}
        aria-label={`${scene.title}. ${scene.outcome}.`}
        className="workflow-scene"
        initial={false}
        animate={{ opacity: 1, y: 0 }}
        exit={reduced ? { opacity: 1 } : { opacity: 0, y: -TRAVEL.base }}
        // A step swapping for the next one is a state change, so it runs at
        // the table's `state` rung on the same `settle` curve every GSAP
        // reveal beside it now uses — `"easeOut"` was `motion/react`'s own
        // default and agreed with nothing else on the page.
        transition={
          reduced
            ? { duration: 0 }
            : { duration: seconds(DURATIONS.state), ease: EASINGS.settle.bezier }
        }
      >
        <header className="workflow-scene-header">
          <div>
            <p className="workflow-scene-kicker">{scene.question}</p>
            <h3>{scene.title}</h3>
          </div>
          <p className="workflow-scene-outcome">{scene.outcome}</p>
        </header>

        <div className="workflow-scene-canvas" aria-hidden="true">
          <svg viewBox="0 0 640 360" preserveAspectRatio="none">
            {scene.paths.map((path) => (
              <path
                key={path.id}
                data-scene-path={path.id}
                data-tone={path.tone}
                pathLength="1"
                d={path.d}
              />
            ))}
          </svg>

          {scene.objects.map((object) => {
            const style: SceneObjectStyle = {
              "--scene-x": `${object.x}%`,
              "--scene-y": `${object.y}%`,
            };

            return (
              <div
                key={object.id}
                data-scene-object={object.id}
                data-tone={object.tone}
                className="workflow-scene-object"
                style={style}
              >
                <i />
                <span>{object.label}</span>
                {object.detail ? <small>{object.detail}</small> : null}
              </div>
            );
          })}
        </div>

        {scene.events.length > 0 ? (
          <div className="workflow-scene-events" aria-label={`${scene.title} protocol events`}>
            {scene.events.map((event) => (
              <span key={event} data-scene-event={event}>{event}</span>
            ))}
          </div>
        ) : (
          <div className="workflow-scene-events" aria-hidden="true">
            <span>Visible system state</span>
          </div>
        )}
      </motion.article>
    </AnimatePresence>
  );
}
