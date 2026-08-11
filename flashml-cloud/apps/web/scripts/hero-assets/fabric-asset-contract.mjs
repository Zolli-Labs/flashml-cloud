import { FABRIC_ASSET_SILHOUETTES } from "../../lib/hero-fabric-assets.mjs";

export const MAX_COMBINED_BYTES = 1_800_000;

const ASSET_FILES = Object.freeze({
  everyday: "everyday-machines.glb",
  owned: "owned-infrastructure.glb",
  rented: "rented-gpu.glb",
  cloud: "cloud-hpc.glb",
  controlPlane: "control-plane.glb",
});

export const REQUIRED_FABRIC_ASSETS = Object.freeze(
  Object.entries(ASSET_FILES).map(([key, file]) =>
    Object.freeze({ key, file, ...FABRIC_ASSET_SILHOUETTES[key] }),
  ),
);

const AXIS_INDEX = Object.freeze({ x: 0, y: 1, z: 2 });
const FORWARD = Object.freeze([0, 0, 1]);

function cleanNumber(value) {
  return Number(value.toFixed(6));
}

function sameValue(left, right) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function sameBounds(left, right, tolerance = 0.001) {
  return ["min", "max"].every(
    (edge) =>
      Array.isArray(left?.[edge]) &&
      Array.isArray(right?.[edge]) &&
      left[edge].length === 3 &&
      right[edge].length === 3 &&
      left[edge].every(
        (value, index) => Math.abs(value - right[edge][index]) <= tolerance,
      ),
  );
}

function isForward(value) {
  return (
    Array.isArray(value) &&
    value.length === FORWARD.length &&
    value.every((component, index) => component === FORWARD[index])
  );
}

export function dimensions(bounds) {
  if (!bounds || !Array.isArray(bounds.min) || !Array.isArray(bounds.max)) {
    return null;
  }
  const values = bounds.max.map((maximum, index) => maximum - bounds.min[index]);
  if (values.length !== 3 || values.some((value) => !Number.isFinite(value))) {
    return null;
  }
  return Object.freeze({ x: values[0], y: values[1], z: values[2] });
}

function semanticDimension(asset, semantic, axis) {
  const axisIndex = AXIS_INDEX[axis];
  const bounds = asset?.semanticBounds?.[semantic];
  if (
    axisIndex === undefined ||
    !Array.isArray(bounds?.min) ||
    !Array.isArray(bounds?.max)
  ) {
    return undefined;
  }
  return bounds.max[axisIndex] - bounds.min[axisIndex];
}

function invalidRatioOperandReason(value) {
  if (!Number.isFinite(value)) return "non-finite";
  if (value <= 0) return "non-positive";
  return null;
}

function ratioLabel(numerator, denominator) {
  return `${numerator[0]}:${numerator[1]}/${denominator[1]}`;
}

function relativeRatioLabel(subject, reference) {
  return `${subject[0]}:${subject[1]}/${reference[0]}:${reference[1]}:${reference[2]}`;
}

export function validateFabricManifest(manifest, stats) {
  const failures = [];
  const manifestAssets = Array.isArray(manifest?.assets) ? manifest.assets : [];
  const inspectedAssets = Object.fromEntries(
    REQUIRED_FABRIC_ASSETS.map((required) => [
      required.key,
      stats.files[required.file]?.inspection,
    ]),
  );

  if (manifest?.schemaVersion !== 2) {
    failures.push({
      code: "unsupported-schema-version",
      expected: 2,
      actual: manifest?.schemaVersion,
    });
  }

  for (const required of REQUIRED_FABRIC_ASSETS) {
    const asset = manifestAssets.find((candidate) => candidate.file === required.file);
    if (!asset) {
      failures.push({ code: "missing-manifest-asset", file: required.file });
      continue;
    }

    if (asset.ownership !== "first-party") {
      failures.push({
        code: "invalid-asset-ownership",
        asset: required.file,
        expected: "first-party",
        actual: asset.ownership,
      });
    }

    const fileStats = stats.files[required.file];
    const inspected = fileStats?.inspection;
    if (!fileStats) {
      failures.push({ code: "missing-required-file", file: required.file });
    } else {
      if (fileStats.byteLength !== asset.byteLength) {
        failures.push({
          code: "byte-length-mismatch",
          file: required.file,
          expected: asset.byteLength,
          actual: fileStats.byteLength,
        });
      }
      if (fileStats.sha256 !== asset.sha256) {
        failures.push({
          code: "content-hash-mismatch",
          file: required.file,
          expected: asset.sha256,
          actual: fileStats.sha256,
        });
      }
      if (!inspected) {
        failures.push({ code: "missing-artifact-inspection", asset: required.file });
      }
    }

    if (!inspected) continue;

    if (!sameValue(asset.meshNames, inspected.meshNames)) {
      failures.push({
        code: "artifact-mesh-names-mismatch",
        asset: required.file,
        expected: asset.meshNames,
        actual: inspected.meshNames,
      });
    }
    if (asset.triangleCount !== inspected.triangleCount) {
      failures.push({
        code: "artifact-triangle-count-mismatch",
        asset: required.file,
        expected: asset.triangleCount,
        actual: inspected.triangleCount,
      });
    }
    if (!sameBounds(asset.boundingBox, inspected.boundingBox)) {
      failures.push({
        code: "artifact-bounding-box-mismatch",
        asset: required.file,
        expected: asset.boundingBox,
        actual: inspected.boundingBox,
      });
    }
    if (!sameValue(asset.forward, inspected.forward)) {
      failures.push({
        code: "artifact-forward-mismatch",
        asset: required.file,
        expected: asset.forward,
        actual: inspected.forward,
      });
    }

    for (const mesh of required.requiredMeshes) {
      if (!inspected.meshNames.includes(mesh)) {
        failures.push({
          code: "missing-required-mesh",
          asset: required.file,
          mesh,
        });
      }
    }

    for (const semantic of required.requiredSemanticBounds) {
      const hasNode = inspected.nodeNames.includes(semantic);
      const actualBounds = inspected.semanticBounds?.[semantic];
      if (!hasNode || !dimensions(actualBounds)) {
        failures.push({
          code: "missing-semantic-bounds",
          asset: required.file,
          semantic,
        });
        continue;
      }
      if (!sameBounds(asset.semanticBounds?.[semantic], actualBounds)) {
        failures.push({
          code: "artifact-semantic-bounds-mismatch",
          asset: required.file,
          semantic,
          expected: asset.semanticBounds?.[semantic],
          actual: actualBounds,
        });
      }
      const actualForward = inspected.semanticForwards?.[semantic];
      if (!isForward(actualForward)) {
        failures.push({
          code: "invalid-artifact-forward-orientation",
          asset: required.file,
          semantic,
          actual: actualForward,
        });
      }
    }

    if (!isForward(asset.forward)) {
      failures.push({
        code: "invalid-forward-orientation",
        asset: required.file,
        actual: asset.forward,
      });
    }

    for (const ratio of required.ratios) {
      const numerator = semanticDimension(inspected, ...ratio.numerator);
      const denominator = semanticDimension(inspected, ...ratio.denominator);
      const label = ratioLabel(ratio.numerator, ratio.denominator);
      const numeratorFailure = invalidRatioOperandReason(numerator);
      const denominatorFailure = invalidRatioOperandReason(denominator);
      if (numeratorFailure) {
        failures.push({
          code: "invalid-silhouette-ratio-numerator",
          asset: required.file,
          ratio: label,
          actual: numerator,
          reason: numeratorFailure,
        });
      }
      if (denominatorFailure) {
        failures.push({
          code: "invalid-silhouette-ratio-denominator",
          asset: required.file,
          ratio: label,
          actual: denominator,
          reason: denominatorFailure,
        });
      }
      if (numeratorFailure || denominatorFailure) {
        continue;
      }
      const actual = cleanNumber(numerator / denominator);
      if (actual < ratio.min) {
        failures.push({
          code: "silhouette-ratio-below-minimum",
          asset: required.file,
          ratio: label,
          actual,
          min: ratio.min,
        });
      }
    }

    for (const ratio of required.relativeRatios) {
      const referenceAsset = inspectedAssets[ratio.reference[0]];
      const subject = semanticDimension(inspected, ...ratio.subject);
      const reference = semanticDimension(referenceAsset, ...ratio.reference.slice(1));
      const label = relativeRatioLabel(ratio.subject, ratio.reference);
      const subjectFailure = invalidRatioOperandReason(subject);
      const referenceFailure = invalidRatioOperandReason(reference);
      if (subjectFailure) {
        failures.push({
          code: "invalid-relative-silhouette-ratio-subject",
          asset: required.file,
          ratio: label,
          actual: subject,
          reason: subjectFailure,
        });
      }
      if (referenceFailure) {
        failures.push({
          code: "invalid-relative-silhouette-ratio-reference",
          asset: required.file,
          ratio: label,
          actual: reference,
          reason: referenceFailure,
        });
      }
      if (subjectFailure || referenceFailure) {
        continue;
      }
      const actual = cleanNumber(subject / reference);
      if (actual < ratio.min) {
        failures.push({
          code: "relative-silhouette-ratio-below-minimum",
          asset: required.file,
          ratio: label,
          actual,
          min: ratio.min,
        });
      }
    }

    const minY = inspected.boundingBox?.min?.[1];
    if (!Number.isFinite(minY) || Math.abs(minY) > 0.02) {
      failures.push({
        code: "invalid-contact-origin",
        file: required.file,
        minY,
      });
    }
  }

  const actualBytes = Object.values(stats.files).reduce(
    (total, file) => total + file.byteLength,
    0,
  );
  if (manifest.totalByteLength !== actualBytes) {
    failures.push({
      code: "total-byte-length-mismatch",
      expected: manifest.totalByteLength,
      actual: actualBytes,
    });
  }
  if (actualBytes > MAX_COMBINED_BYTES) {
    failures.push({
      code: "combined-budget-exceeded",
      actualBytes,
      maxBytes: MAX_COMBINED_BYTES,
    });
  }

  if (!stats.licenseFiles.includes(manifest.licenseFile)) {
    failures.push({
      code: "missing-license-file",
      file: manifest.licenseFile,
    });
  }

  return { valid: failures.length === 0, failures };
}
