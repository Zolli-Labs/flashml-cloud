import { createHash } from "node:crypto";
import { access, readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import {
  REQUIRED_FABRIC_ASSETS,
  validateFabricManifest,
} from "./fabric-asset-contract.mjs";
import { inspectFabricGlb } from "./inspect-fabric-glb.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(SCRIPT_DIR, "../..");
const DEFAULT_INPUT = join(WEB_ROOT, "public/models/hero/fabric");

function parseInputArgument(argv) {
  const index = argv.indexOf("--input");
  if (index === -1) return DEFAULT_INPUT;
  if (!argv[index + 1]) throw new Error("--input requires a directory");
  return resolve(process.cwd(), argv[index + 1]);
}

async function exists(path) {
  try {
    await access(path);
    return true;
  } catch {
    return false;
  }
}

async function main() {
  const inputDirectory = parseInputArgument(process.argv.slice(2));
  const manifest = JSON.parse(
    await readFile(join(inputDirectory, "asset-manifest.json"), "utf8"),
  );
  const files = {};

  for (const required of REQUIRED_FABRIC_ASSETS) {
    const path = join(inputDirectory, required.file);
    if (!(await exists(path))) continue;
    const contents = await readFile(path);
    files[required.file] = {
      byteLength: contents.byteLength,
      sha256: createHash("sha256").update(contents).digest("hex"),
      inspection: await inspectFabricGlb(
        contents,
        required.requiredSemanticBounds,
      ),
    };
  }

  const licenseFiles = (await exists(join(inputDirectory, manifest.licenseFile)))
    ? [manifest.licenseFile]
    : [];
  const result = validateFabricManifest(manifest, { files, licenseFiles });

  if (!result.valid) {
    process.stderr.write("Fabric asset validation failed:\n");
    for (const failure of result.failures) {
      process.stderr.write(`- ${JSON.stringify(failure)}\n`);
    }
    process.exitCode = 1;
    return;
  }

  for (const asset of manifest.assets) {
    process.stdout.write(
      `OK ${asset.file}: ${asset.byteLength} bytes, ${asset.triangleCount} triangles, ${asset.meshNames.length} named meshes, sha256 ${asset.sha256}\n`,
    );
  }
  process.stdout.write(
    `Fabric asset validation passed: ${manifest.assets.length} GLBs, ${manifest.totalByteLength} bytes total, license ${manifest.licenseFile}.\n`,
  );
}

await main();
