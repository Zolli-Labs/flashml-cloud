"use client";

import { Button } from "@/components/ui/button";
import { TemplateGlyph } from "@/components/deploy/TemplateGlyph";
import {
  DEPLOY_TEMPLATES,
  templateMeta,
  type DeployTemplate,
} from "@/lib/deploy/templates";

/**
 * Five complete jobs, as the first thing the Deploy page shows.
 *
 * WHAT THIS REPLACES. The submit page opened on an empty text field asking
 * for a GitHub URL, which is a fine flow for somebody who already has a
 * repository with a `flashml.yaml` in it and no flow at all for anybody
 * else. There is no way in from that screen: nothing on it says what a
 * `flashml.yaml` contains, and the five worked examples that answer that
 * question live in another repository entirely.
 *
 * WHAT A CARD IS ALLOWED TO SAY. The name, the router's own tag for the
 * shape, one line about what the workload demonstrates, and the two facts a
 * reader plans around — how much work it is and whether it needs a card.
 * Everything else is in the yaml, which is one click away and is the actual
 * document. A card that summarised the config would be a second, worse copy
 * of it.
 *
 * The Deploy button does NOT submit — see `TEMPLATE_SUBMIT_GAP`. It fills
 * the editor with this template's yaml and moves the reader there.
 */
export function TemplateGallery({
  selectedId,
  onDeploy,
}: {
  /** The template already loaded into the editor, if any. Marked on its
   * card so a reader coming back from the yaml tab can see which one they
   * are holding rather than having to recognise the text. */
  selectedId: string | null;
  onDeploy: (template: DeployTemplate) => void;
}) {
  return (
    <div>
      <p className="max-w-prose text-sm text-muted-foreground">
        Complete jobs from the public examples. Deploy loads one into the
        editor; nothing is submitted.
      </p>

      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {DEPLOY_TEMPLATES.map((template) => (
          <TemplateCard
            key={template.id}
            template={template}
            selected={template.id === selectedId}
            onDeploy={onDeploy}
          />
        ))}
      </ul>
    </div>
  );
}

function TemplateCard({
  template,
  selected,
  onDeploy,
}: {
  template: DeployTemplate;
  selected: boolean;
  onDeploy: (template: DeployTemplate) => void;
}) {
  return (
    <li
      className={`panel flex flex-col p-4 ${
        selected ? "border-brand/50" : ""
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-medium">{template.name}</h3>
          {/* The tag chip, in the same shape `SourceChip` uses on the market
              board: mono, small caps, bordered, no fill. It is a value the
              router answers with, not a label this page invented — see
              `WORKLOAD_TAGS`. */}
          <span className="mt-1.5 inline-flex items-center rounded-full border border-border px-1.5 py-0.5 font-mono text-[10px] tracking-wide text-muted-foreground">
            {template.tag}
          </span>
        </div>
        <div className="text-foreground/70">
          <TemplateGlyph id={template.id} />
        </div>
      </div>

      {/* `flex-1` so the Deploy buttons line up across a row whatever the
          length of the line above them. */}
      <p className="mt-3 flex-1 text-sm leading-relaxed text-muted-foreground">
        {template.demonstrates}
      </p>

      <p className="meta mt-3">{templateMeta(template)}</p>

      {/* `sm`, not the default height, and that is a decision about the
          screen rather than about the button: five equal cards means five
          equal CTAs, and at the default size that is a block of brand
          orange large enough to be the loudest thing on a page whose actual
          commitment (Submit) is one tab away. Small keeps the affordance
          and gives the glyphs and the copy the weight.

          The selected card drops to `outline` because its button no longer
          offers the same thing — the template is already in the editor, and
          pressing it again discards edits. */}
      <Button
        type="button"
        variant={selected ? "outline" : "default"}
        size="sm"
        className="mt-3 w-full"
        onClick={() => onDeploy(template)}
      >
        {selected ? "Reload in editor" : "Deploy"}
      </Button>
    </li>
  );
}
