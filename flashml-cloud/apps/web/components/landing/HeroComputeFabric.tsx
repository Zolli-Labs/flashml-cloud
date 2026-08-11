"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Check, Cloud, Cpu, HouseLine, Stack } from "@phosphor-icons/react";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { getFabricRenderDecision, type FabricRenderDecision } from "@/lib/hero-fabric";
import {
  createHeroRuntimeState,
  getHeroJobStep,
  getHeroSource,
  getNextHeroJobStep,
  HERO_JOB_STEPS,
  HERO_SOURCES,
  reduceHeroRuntime,
  type HeroJobStepKey,
  type HeroRuntimeState,
  type HeroSourceKey,
} from "@/lib/hero-story";
import styles from "./HeroComputeFabric.module.css";

const HeroFabricCanvas = dynamic(
  () => import("./hero-fabric/HeroFabricCanvas").then((module) => module.HeroFabricCanvas),
  {
    ssr: false,
    loading: () => (
      <div className={styles.canvasLoading} aria-label="Loading compute fabric">
        Preparing compute fabric…
      </div>
    ),
  },
);

const SOURCE_ICONS = {
  cloud: Cloud,
  rented: Cpu,
  owned: Stack,
  everyday: HouseLine,
} as const;

function detectWebGL2Support() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2"));
  } catch {
    return false;
  }
}

export function HeroComputeFabric() {
  const { reduced, desktop, finePointer, documentVisible } = useLandingMotion();
  const initialRuntime: HeroRuntimeState = createHeroRuntimeState();
  const [runtime, dispatch] = useReducer(reduceHeroRuntime, initialRuntime);
  const {
    selectedSource,
    jobStep,
    storyPlaying,
    fabricFailed,
    fabricEntranceComplete,
  } = runtime;
  const [webgl2, setWebgl2] = useState<boolean | null>(null);
  const [focusedSource, setFocusedSource] = useState<HeroSourceKey | null>(null);
  const motionInitialized = useRef(false);
  const fabricDecision = useMemo<FabricRenderDecision | null>(
    () => webgl2 === null
      ? null
      : getFabricRenderDecision({
          webgl2,
          reducedMotion: reduced,
          documentVisible,
          desktop,
          finePointer,
        }),
    [desktop, documentVisible, finePointer, reduced, webgl2],
  );
  const storyUnavailable = fabricFailed || fabricDecision?.mode === "poster";

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      setWebgl2(detectWebGL2Support());
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      if (reduced) {
        dispatch({ type: "set-story-playing", playing: false });
        return;
      }
      if (
        documentVisible
        && fabricDecision?.mode === "canvas"
        && !motionInitialized.current
      ) {
        motionInitialized.current = true;
        dispatch({ type: "initialize-motion" });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [documentVisible, fabricDecision, reduced]);

  useEffect(() => {
    if (reduced || !documentVisible || !storyPlaying) return;
    const activeStep = getHeroJobStep(jobStep);
    const timer = window.setTimeout(
      () => dispatch({
        type: "advance-job-step",
        jobStep: getNextHeroJobStep(jobStep),
      }),
      activeStep.durationMs,
    );
    return () => window.clearTimeout(timer);
  }, [documentVisible, jobStep, reduced, storyPlaying]);

  const activeSource = useMemo(() => getHeroSource(selectedSource), [selectedSource]);
  const activeJobStep = useMemo(() => getHeroJobStep(jobStep), [jobStep]);
  const markFabricFailure = useCallback(() => dispatch({ type: "fabric-failure" }), []);
  const markFabricEntranceComplete = useCallback(
    () => dispatch({ type: "fabric-entrance-complete" }),
    [],
  );
  const markManualInteraction = useCallback(() => {
    motionInitialized.current = true;
  }, []);
  const selectSource = useCallback((source: HeroSourceKey) => {
    markManualInteraction();
    setFocusedSource(source);
    dispatch({ type: "select-source", source });
  }, [markManualInteraction]);
  const selectJobStep = useCallback((nextJobStep: HeroJobStepKey) => {
    markManualInteraction();
    setFocusedSource(null);
    dispatch({ type: "select-job-step", jobStep: nextJobStep });
  }, [markManualInteraction]);
  const toggleStory = useCallback(() => {
    if (storyUnavailable) return;
    markManualInteraction();
    if (!storyPlaying) setFocusedSource(null);
    dispatch({ type: "toggle-story", reducedMotion: reduced });
  }, [markManualInteraction, reduced, storyPlaying, storyUnavailable]);

  return (
    <div data-hero-fabric="production" className={styles.fabric}>
      <div className={styles.sceneFrame} data-scene-boundary="true">
        <div className={styles.canvasWrap}>
          <div className={styles.canvasHeading} aria-hidden="true">
            <span>One Zolli control plane</span>
            <span>{activeJobStep.label}</span>
          </div>
          <HeroFabricCanvas
            selectedSource={selectedSource}
            focusedSource={focusedSource}
            jobStep={jobStep}
            reducedMotion={reduced}
            documentVisible={documentVisible}
            fabricDecision={fabricDecision}
            fabricEntranceComplete={fabricEntranceComplete}
            fabricFailed={fabricFailed}
            finePointer={finePointer}
            storyPlaying={storyPlaying}
            onFabricEntranceComplete={markFabricEntranceComplete}
            onFabricFailure={markFabricFailure}
            onSelectSource={selectSource}
          />
          <div className={styles.controlCaption} aria-hidden="true">
            <span>Zolli coordinates the crew</span>
            <span>Checkpoint retained · work resumes elsewhere</span>
          </div>
        </div>

        <aside className={styles.sourcePanel} aria-label="Compute source">
          <p className={styles.panelLabel}>Select source</p>
          <div className={styles.sourceButtons}>
            {HERO_SOURCES.map((source) => {
              const active = source.key === selectedSource;
              const Icon = SOURCE_ICONS[source.key];
              return (
                <button
                  key={source.key}
                  type="button"
                  aria-pressed={active}
                  className={styles.sourceButton}
                  data-active={active ? "true" : "false"}
                  onClick={() => selectSource(source.key)}
                >
                  <Icon aria-hidden weight="regular" />
                  <span>{source.shortLabel}</span>
                  <i aria-hidden />
                </button>
              );
            })}
          </div>

          <div className={styles.sourceDetail} aria-live="polite">
            <p>{activeSource.label}</p>
            <span>{activeSource.meaning}</span>
            <ul>
              {activeSource.examples.map((example) => (
                <li key={example}>
                  <Check aria-hidden weight="bold" />
                  {example}
                </li>
              ))}
            </ul>
            <strong>{activeSource.outcome}</strong>
          </div>
        </aside>
      </div>

      <div className={styles.storyHeader}>
        <div>
          <p>One recoverable execution</p>
          <span aria-live="polite">{activeJobStep.label}</span>
        </div>
        <button
          type="button"
          aria-pressed={storyPlaying}
          aria-disabled={storyUnavailable}
          disabled={storyUnavailable}
          onClick={toggleStory}
        >
          {reduced ? "Static story" : storyPlaying ? "Pause story" : "Play story"}
        </button>
      </div>

      <ol className={styles.jobRail} aria-label="Inspect job state">
        {HERO_JOB_STEPS.map((step, index) => {
          const active = step.key === jobStep;
          return (
            <li key={step.key} data-step={step.key} data-active={active ? "true" : "false"}>
              <button
                type="button"
                aria-current={active ? "step" : undefined}
                onClick={() => selectJobStep(step.key)}
              >
                <i aria-hidden>{String(index + 1).padStart(2, "0")}</i>
                <span>{step.label}</span>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
