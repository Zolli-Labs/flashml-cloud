import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Mark, Wordmark } from "@/components/brand/Mark";
import { ZolliCharacter } from "@/components/brand/ZolliCharacter";
import { ZOLLI_ROLES } from "@/lib/zolli-brand";

function withDecodedAssetPaths(markup: string) {
  return markup.replaceAll("%2F", "/");
}

describe("Zolli role system", () => {
  it("defines the six approved product roles", () => {
    expect(Object.keys(ZOLLI_ROLES)).toEqual([
      "captain",
      "worker",
      "scout",
      "keeper",
      "relay",
      "builder",
    ]);
    expect(ZOLLI_ROLES.keeper.subtitle).toBe("Checkpoint");
    expect(ZOLLI_ROLES.relay.subtitle).toBe("Handoff");
  });
});

describe("canonical Zolli artwork", () => {
  it("renders the official symbol and horizontal logo assets", () => {
    const mark = withDecodedAssetPaths(renderToStaticMarkup(createElement(Mark)));
    const wordmark = withDecodedAssetPaths(
      renderToStaticMarkup(createElement(Wordmark, { product: true })),
    );

    expect(mark).toContain("/brand/logos/logo-symbol-orange.png");
    expect(wordmark).toContain("/brand/logos/logo-primary.png");
    expect(wordmark).toContain("Cloud");
  });

  it.each(Object.keys(ZOLLI_ROLES) as Array<keyof typeof ZOLLI_ROLES>)(
    "maps %s to its official standalone character",
    (role) => {
      const markup = withDecodedAssetPaths(
        renderToStaticMarkup(createElement(ZolliCharacter, { role })),
      );

      expect(markup).toContain(`/brand/characters/${role}.png`);
    },
  );
});

describe("ZolliCharacter accessibility", () => {
  it("exposes a supplied character label and hides decorative characters", () => {
    const labelledCharacter = ZolliCharacter({
      role: "captain",
      label: "Captain Zolli",
    });
    const decorativeCharacter = ZolliCharacter({ role: "worker" });

    expect(labelledCharacter.props).toMatchObject({
      role: "img",
      "aria-label": "Captain Zolli",
    });
    expect(labelledCharacter.props["aria-hidden"]).toBeUndefined();
    expect(decorativeCharacter.props).toMatchObject({ "aria-hidden": true });
    expect(decorativeCharacter.props.role).toBeUndefined();
  });

  it("limits animated characters to motion-safe CSS and disables them for reduced motion", () => {
    const markup = renderToStaticMarkup(
      createElement(ZolliCharacter, { role: "captain", animated: true }),
    );

    expect(markup).toContain("motion-safe:animate-bounce");
    expect(markup).toContain("motion-reduce:animate-none");
    expect(markup).not.toContain("<animateTransform");
  });
});
