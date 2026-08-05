import {
  siNumpy,
  siPandas,
  siPython,
  siPytorch,
  siScikitlearn,
  siScipy,
} from "simple-icons";
import { Reveal, RevealGroup, RevealItem } from "@/components/motion/Reveal";

// "Works with" rather than "trusted by".
//
// A customer logo wall would be a lie — there are no customers. Framework
// logos are a different claim: they say what your code can import, which is
// checkable, and it is the question a reader actually has before they try
// this. Everything below is read off apps/api/flashml_cloud_api/images.py,
// which is a CLOSED set of exactly three images, so the section says that
// out loud rather than implying you can bring anything.
//
// Official marks from `simple-icons` instead of hand-drawn paths, rendered
// as inline SVG so there is no CDN request and nothing to break offline.
// Rendered in the foreground colour, not brand colours: six saturated logos
// on a dark page would be the loudest thing on it, and the palette rule here
// reserves colour for state.

const IMAGES = [
  {
    alias: "python-slim",
    blurb: "CPython 3.11, standard library only.",
    icons: [siPython],
  },
  {
    alias: "sklearn",
    blurb: "Classical ML and dataframes.",
    icons: [siScikitlearn, siNumpy, siPandas, siScipy],
  },
  {
    alias: "pytorch-cpu",
    // The honest qualifier. There is no GPU image, and a bare PyTorch logo
    // would let a reader assume otherwise — which is exactly the assumption
    // that wastes their afternoon.
    blurb: "PyTorch, CPU build. GPU images are not available yet.",
    icons: [siPytorch, siNumpy],
  },
] as const;

function Logo({ icon }: { icon: { title: string; path: string } }) {
  return (
    <svg
      role="img"
      aria-label={icon.title}
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0 fill-current"
    >
      <title>{icon.title}</title>
      <path d={icon.path} />
    </svg>
  );
}

export function FrameworkStack() {
  return (
    <section className="mx-auto max-w-7xl px-4 py-20 sm:px-6 md:py-28">
      <Reveal className="max-w-2xl">
        <h2 className="title">Bring code, not containers.</h2>
        <p className="mt-4 text-base leading-relaxed text-muted-foreground">
          Your repo declares one of three pinned images. Preflight diffs your
          imports against what that image actually contains, so an unknown
          import fails before anything is scheduled instead of on somebody
          else&apos;s machine.
        </p>
      </Reveal>

      <RevealGroup className="mt-12 grid gap-4 md:grid-cols-3" stagger={0.08}>
        {IMAGES.map((img) => (
          <RevealItem key={img.alias}>
            <div className="panel h-full p-5">
              <div className="flex items-center gap-3 text-foreground/80">
                {img.icons.map((i) => (
                  <Logo key={i.title} icon={i} />
                ))}
              </div>
              <div className="mt-4 font-mono text-sm text-brand-foreground">
                {img.alias}
              </div>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {img.blurb}
              </p>
            </div>
          </RevealItem>
        ))}
      </RevealGroup>

      <Reveal delay={0.12}>
        <p className="meta mt-6">
          A closed set, pinned by digest. Custom images are not supported yet.
        </p>
      </Reveal>
    </section>
  );
}
