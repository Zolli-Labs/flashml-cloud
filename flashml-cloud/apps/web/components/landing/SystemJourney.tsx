"use client";

import { useRef, useState } from "react";
import { useGSAP } from "@gsap/react";
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { WorkflowScene } from "@/components/landing/WorkflowScene";
import { useLandingMotion } from "@/components/landing/motion/LandingMotionProvider";
import { WORKFLOW_STEPS } from "@/lib/landing/workflow";

gsap.registerPlugin(useGSAP, ScrollTrigger);

export function SystemJourney() {
  const rootRef = useRef<HTMLElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const stepsRef = useRef<HTMLOListElement>(null);
  const [activeIndex, setActiveIndex] = useState(0);
  const { reduced, desktop } = useLandingMotion();
  const usesPinnedStory = desktop && !reduced;

  useGSAP(
    () => {
      const root = rootRef.current;
      const stage = stageRef.current;
      const steps = stepsRef.current;
      if (!root || !stage || !steps || !usesPinnedStory) return;

      const stepItems = gsap.utils.toArray<HTMLElement>(
        steps.querySelectorAll("[data-workflow-step]"),
      );
      const triggers = stepItems.map((item, index) => ScrollTrigger.create({
        trigger: item,
        start: "top center",
        end: "bottom center",
        onEnter: () => setActiveIndex(index),
        onEnterBack: () => setActiveIndex(index),
      }));
      const pin = ScrollTrigger.create({
        trigger: stage,
        pin: stage,
        pinSpacing: false,
        start: "top top+=104",
        endTrigger: steps,
        end: "bottom bottom-=72",
        invalidateOnRefresh: true,
      });

      return () => {
        pin.kill();
        triggers.forEach((trigger) => trigger.kill());
      };
    },
    {
      scope: rootRef,
      dependencies: [usesPinnedStory],
      revertOnUpdate: true,
    },
  );

  return (
    <section
      ref={rootRef}
      id="technical-workflow"
      data-surface="dark"
      data-motion="system-journey"
      className="scroll-mt-20 border-b border-white/10 py-20 text-[var(--landing-ivory)] md:py-28"
    >
      <div className="mx-auto max-w-[1240px] px-5 sm:px-6">
        <div className="grid gap-8 border-b border-white/12 pb-10 md:grid-cols-[.72fr_1.28fr] md:gap-12 md:pb-14">
          <p className="font-mono text-[11px] font-medium uppercase tracking-[0.13em] text-[var(--landing-orange)]">
            Technical workflow
          </p>
          <div>
            <h2 className="max-w-[820px] text-[clamp(2.65rem,5.4vw,4.75rem)] font-semibold leading-[0.99] tracking-[-0.052em]">
              See how one job moves through the runtime.
            </h2>
            <p className="mt-6 max-w-[62ch] text-base leading-relaxed text-white/58">
              For technical evaluators, seven scenes show how capacity joins, work moves, progress survives, and one result is accepted.
            </p>
          </div>
        </div>

        <div className="mt-10 grid items-start gap-10 lg:grid-cols-[minmax(0,.72fr)_minmax(440px,1.28fr)] lg:gap-14">
          <ol
            ref={stepsRef}
            aria-label="Zolli system workflow"
            className="min-w-0"
          >
            {WORKFLOW_STEPS.map((step, index) => (
              <li
                key={step.id}
                data-workflow-step={step.id}
                data-active={usesPinnedStory ? (index === activeIndex ? "true" : "false") : undefined}
                className="border-t border-white/12 py-8 first:border-t-0 lg:flex lg:min-h-[58vh] lg:flex-col lg:justify-center lg:py-12"
              >
                <div className="flex items-start justify-between gap-4">
                  <span className="font-mono text-[10px] uppercase tracking-[0.16em] text-white/48">
                    {step.eyebrow}
                  </span>
                  <span className="max-w-[58%] text-right font-mono text-[9px] font-medium uppercase tracking-[0.08em] text-[var(--landing-orange)]">
                    {step.outcome}
                  </span>
                </div>
                <h3 className="mt-5 text-[clamp(1.6rem,3vw,2.45rem)] font-semibold leading-tight tracking-[-0.035em]">
                  {step.headline}
                </h3>
                <p className="mt-4 max-w-[48ch] text-[15px] leading-relaxed text-white/62">
                  {step.body}
                </p>

                {!usesPinnedStory ? (
                  <div className="mt-7">
                    <WorkflowScene step={step.id} compact />
                  </div>
                ) : null}
              </li>
            ))}
          </ol>

          <div
            ref={stageRef}
            data-workflow-stage
            data-pinned-story={usesPinnedStory ? "true" : "false"}
            className={`hidden min-w-0 lg:col-start-2 lg:row-start-1 ${usesPinnedStory ? "lg:block" : ""}`}
          >
            {usesPinnedStory ? (
              <WorkflowScene step={WORKFLOW_STEPS[activeIndex].id} />
            ) : null}
          </div>
        </div>
      </div>
    </section>
  );
}
