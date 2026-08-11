import { createHash } from "node:crypto";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

import {
  Box3,
  BoxGeometry,
  CylinderGeometry,
  ExtrudeGeometry,
  Group,
  Mesh,
  MeshStandardMaterial,
  Scene,
  Shape,
} from "three";
import { GLTFExporter } from "three/addons/exporters/GLTFExporter.js";
import { RoundedBoxGeometry } from "three/addons/geometries/RoundedBoxGeometry.js";

import { REQUIRED_FABRIC_ASSETS } from "./fabric-asset-contract.mjs";
import { inspectFabricGlb } from "./inspect-fabric-glb.mjs";

const SCRIPT_DIR = dirname(fileURLToPath(import.meta.url));
const WEB_ROOT = resolve(SCRIPT_DIR, "../..");
const DEFAULT_OUTPUT = join(WEB_ROOT, "public/models/hero/fabric");
const GLTF_TRANSFORM = join(
  WEB_ROOT,
  "node_modules/.bin",
  process.platform === "win32" ? "gltf-transform.cmd" : "gltf-transform",
);

const MATERIALS = Object.freeze({
  graphite: material("Mat_GraphiteMetal", 0x51565a, 0.64, 0.34),
  infrastructureFace: material("Mat_InfrastructureFace", 0x626b72, 0.38, 0.46),
  polymer: material("Mat_DarkPolymer", 0x101114, 0.08, 0.42),
  glass: new MeshStandardMaterial({
    name: "Mat_SmokedGlass",
    color: 0x111b22,
    metalness: 0.1,
    roughness: 0.2,
    transparent: true,
    opacity: 0.84,
  }),
  screen: material("Mat_MutedScreen", 0x527382, 0.04, 0.34, 0x2d4852, 0.42),
  orange: material("Mat_ZolliOrangeEmissive", 0xd9682d, 0.2, 0.28, 0xff6f32, 2.5),
  green: material("Mat_VerifiedGreenEmissive", 0x4c9a72, 0.12, 0.34, 0x4fd39a, 2),
});

class NodeFileReader {
  result = null;
  error = null;
  onloadend = null;
  onerror = null;

  readAsArrayBuffer(blob) {
    blob
      .arrayBuffer()
      .then((buffer) => {
        this.result = buffer;
        this.onloadend?.();
      })
      .catch((error) => {
        this.error = error;
        this.onerror?.(error);
      });
  }

  readAsDataURL(blob) {
    blob
      .arrayBuffer()
      .then((buffer) => {
        this.result = `data:${blob.type};base64,${Buffer.from(buffer).toString("base64")}`;
        this.onloadend?.();
      })
      .catch((error) => {
        this.error = error;
        this.onerror?.(error);
      });
  }
}

globalThis.FileReader ??= NodeFileReader;

function material(name, color, metalness, roughness, emissive = 0x000000, intensity = 0) {
  const options = {
    name,
    color,
    metalness,
    roughness,
  };
  if (intensity > 0) {
    options.emissive = emissive;
    options.emissiveIntensity = intensity;
  }
  return new MeshStandardMaterial(options);
}

function rounded(width, height, depth, radius = 0.08, segments = 3) {
  return new RoundedBoxGeometry(width, height, depth, segments, radius);
}

function addMesh(parent, geometry, materialValue, name, position, rotation = [0, 0, 0]) {
  const value = new Mesh(geometry, materialValue);
  value.name = name;
  value.position.set(...position);
  value.rotation.set(...rotation);
  value.castShadow = true;
  value.receiveShadow = true;
  parent.add(value);
  return value;
}

function addScreen(parent, prefix, position, size, rotation = [0, 0, 0]) {
  addMesh(
    parent,
    rounded(size[0] + 0.12, size[1] + 0.12, 0.1, 0.045, 3),
    MATERIALS.polymer,
    `${prefix}_ScreenBezel`,
    position,
    rotation,
  );
  const recess = [position[0], position[1], position[2] + 0.058];
  addMesh(
    parent,
    new BoxGeometry(size[0], size[1], 0.018),
    MATERIALS.screen,
    `${prefix}_ScreenRecess`,
    recess,
    rotation,
  );
}

function addVentRow(parent, prefix, start, count, spacing, geometry, rotation = [0, 0, 0]) {
  for (let index = 0; index < count; index += 1) {
    addMesh(
      parent,
      geometry,
      MATERIALS.polymer,
      `${prefix}_Vent_${String(index + 1).padStart(2, "0")}`,
      [start[0] + index * spacing, start[1], start[2]],
      rotation,
    );
  }
}

function addExtrudedBrace(parent, name, position, scale = 1) {
  const shape = new Shape();
  shape.moveTo(-0.4, -0.18);
  shape.lineTo(0.18, -0.18);
  shape.lineTo(0.42, 0);
  shape.lineTo(0.18, 0.18);
  shape.lineTo(-0.4, 0.18);
  shape.lineTo(-0.25, 0);
  shape.closePath();
  const geometry = new ExtrudeGeometry(shape, {
    depth: 0.07,
    bevelEnabled: true,
    bevelSegments: 2,
    steps: 1,
    bevelSize: 0.025,
    bevelThickness: 0.025,
  });
  geometry.scale(scale, scale, scale);
  addMesh(parent, geometry, MATERIALS.graphite, name, position);
}

function biasPresentation(assembly, name, pivot, yaw) {
  const presentation = new Group();
  presentation.name = name;
  presentation.position.set(pivot[0], 0, pivot[1]);
  presentation.rotation.y = yaw;

  for (const child of [...assembly.children]) {
    assembly.remove(child);
    child.position.x -= pivot[0];
    child.position.z -= pivot[1];
    presentation.add(child);
  }
  assembly.add(presentation);
}

function createEverydayScene() {
  const scene = namedScene("EverydayMachines");
  const laptop = new Group();
  laptop.name = "EverydayLaptopAssembly";
  addMesh(laptop, rounded(1.12, 0.09, 0.72, 0.055), MATERIALS.graphite, "Everyday_Laptop", [-1.45, 0.095, 0.42]);
  addScreen(laptop, "Everyday_Laptop", [-1.45, 0.58, 0.73], [0.96, 0.55]);
  const keyGeometry = rounded(0.095, 0.018, 0.07, 0.012, 2);
  for (let row = 0; row < 3; row += 1) {
    for (let column = 0; column < 8; column += 1) {
      addMesh(
        laptop,
        keyGeometry,
        MATERIALS.polymer,
        `Everyday_Laptop_Key_${row + 1}_${column + 1}`,
        [-1.78 + column * 0.095, 0.151, 0.25 + row * 0.09],
      );
    }
  }
  biasPresentation(laptop, "EverydayLaptopPresentation", [-1.45, 0.42], 0.36);
  scene.add(laptop);

  const workstation = new Group();
  workstation.name = "EverydayWorkstationAssembly";
  addMesh(workstation, rounded(0.86, 0.1, 0.42, 0.045), MATERIALS.graphite, "Everyday_Workstation", [0.05, 0.1, 0.52]);
  addMesh(workstation, rounded(0.5, 0.06, 0.3, 0.025, 2), MATERIALS.graphite, "Everyday_Workstation_Base", [0.05, 0.03, 0.52]);
  addMesh(workstation, new CylinderGeometry(0.08, 0.11, 0.46, 20), MATERIALS.graphite, "Everyday_Workstation_Stand", [0.05, 0.36, 0.52]);
  addScreen(workstation, "Everyday_Workstation", [0.05, 0.82, 0.55], [0.92, 0.58]);
  biasPresentation(workstation, "EverydayWorkstationPresentation", [0.05, 0.52], 0.36);
  scene.add(workstation);

  const tower = new Group();
  tower.name = "EverydayTowerAssembly";
  addMesh(tower, rounded(0.52, 1.05, 0.72, 0.065), MATERIALS.graphite, "Everyday_Tower", [1.55, 0.525, 0.28]);
  const towerFan = new CylinderGeometry(0.19, 0.19, 0.055, 24);
  addMesh(tower, towerFan, MATERIALS.infrastructureFace, "Everyday_Tower_FanHousing", [1.55, 0.67, 0.662], [Math.PI / 2, 0, 0]);
  addMesh(tower, new CylinderGeometry(0.19 * 0.23, 0.19 * 0.23, 0.08, 16), MATERIALS.orange, "Everyday_Tower_FanHub", [1.55, 0.67, 0.717], [Math.PI / 2, 0, 0]);
  addVentRow(tower, "Everyday_Tower", [1.4, 0.27, 0.652], 6, 0.06, new BoxGeometry(0.025, 0.16, 0.025));
  biasPresentation(tower, "EverydayTowerPresentation", [1.55, 0.28], 0.36);
  tower.position.set(0.25, 0, -0.1);
  scene.add(tower);

  const server = new Group();
  server.name = "EverydayHomeServerAssembly";
  addMesh(server, rounded(0.95, 0.48, 0.66, 0.065), MATERIALS.graphite, "Everyday_HomeServer", [0.1, 0.24, -0.72]);
  const homeServerBay = rounded(0.17, 0.12, 0.035, 0.018, 2);
  for (let index = 0; index < 4; index += 1) {
    addMesh(
      server,
      homeServerBay,
      MATERIALS.infrastructureFace,
      `Everyday_HomeServer_Bay_${String(index + 1).padStart(2, "0")}`,
      [-0.2 + index * 0.2, 0.26, -0.372],
    );
  }
  addVentRow(server, "Everyday_HomeServer", [-0.18, 0.4, -0.37], 8, 0.08, new BoxGeometry(0.032, 0.05, 0.022));
  addMesh(server, new CylinderGeometry(0.026, 0.026, 0.025, 12), MATERIALS.green, "Everyday_HomeServer_StatusLED", [0.48, 0.4, -0.365], [Math.PI / 2, 0, 0]);
  biasPresentation(server, "EverydayHomeServerPresentation", [0.1, -0.72], 0.36);
  server.position.set(-0.5, 0, -0.24);
  scene.add(server);
  return scene;
}

function createOwnedScene() {
  const scene = namedScene("OwnedInfrastructure");
  const workstation = new Group();
  workstation.name = "OwnedWorkstationAssembly";
  addMesh(workstation, rounded(0.74, 1.2, 0.82, 0.075), MATERIALS.graphite, "Owned_Workstation", [-0.72, 0.6, 0.08]);
  const workstationFan = new CylinderGeometry(0.17, 0.17, 0.06, 24);
  for (const [index, y] of [0.46, 0.88].entries()) {
    addMesh(workstation, workstationFan, MATERIALS.infrastructureFace, `Owned_Workstation_Fan_${String(index + 1).padStart(2, "0")}`, [-0.72, y, 0.506], [Math.PI / 2, 0, 0]);
    addMesh(workstation, new CylinderGeometry(0.045, 0.045, 0.07, 16), MATERIALS.orange, `Owned_Workstation_FanHub_${String(index + 1).padStart(2, "0")}`, [-0.72, y, 0.542], [Math.PI / 2, 0, 0]);
  }
  addVentRow(workstation, "Owned_Workstation", [-0.91, 0.2, 0.505], 6, 0.075, new BoxGeometry(0.034, 0.11, 0.024));
  const gpuBayGeometry = rounded(0.055, 0.22, 0.42, 0.018, 2);
  addMesh(workstation, gpuBayGeometry, MATERIALS.infrastructureFace, "Owned_Workstation_GPU_Bay_01", [-0.338, 0.42, 0.02]);
  addMesh(workstation, gpuBayGeometry, MATERIALS.infrastructureFace, "Owned_Workstation_GPU_Bay_02", [-0.338, 0.72, 0.02]);
  addExtrudedBrace(workstation, "Owned_Workstation_Handle", [-0.72, 1.29, 0.03], 0.62);
  biasPresentation(workstation, "OwnedWorkstationPresentation", [-0.72, 0.08], 0.82);
  workstation.position.x = -0.12;
  scene.add(workstation);

  const rack = new Group();
  rack.name = "OwnedRackAssembly";
  addMesh(rack, rounded(1.06, 1.86, 0.82, 0.075), MATERIALS.graphite, "Owned_Rack", [0.62, 0.93, 0]);
  addMesh(rack, new BoxGeometry(0.92, 1.62, 0.045), MATERIALS.glass, "Owned_Rack_SmokedDoor", [0.62, 0.93, 0.433]);
  const ownedRailGeometry = rounded(0.055, 1.64, 0.065, 0.018, 2);
  addMesh(rack, ownedRailGeometry, MATERIALS.infrastructureFace, "Owned_Rack_LeftRail", [0.17, 0.93, 0.46]);
  addMesh(rack, ownedRailGeometry, MATERIALS.infrastructureFace, "Owned_Rack_RightRail", [1.07, 0.93, 0.46]);
  for (let row = 0; row < 7; row += 1) {
    const y = 0.27 + row * 0.225;
    addMesh(rack, rounded(0.88, 0.15, 0.11, 0.025, 2), MATERIALS.infrastructureFace, `Owned_Rack_Bay_${String(row + 1).padStart(2, "0")}`, [0.62, y, 0.46]);
    addMesh(rack, new CylinderGeometry(0.018, 0.018, 0.025, 10), row === 6 ? MATERIALS.green : MATERIALS.orange, `Owned_Rack_LED_${String(row + 1).padStart(2, "0")}`, [1.05, y, 0.53], [Math.PI / 2, 0, 0]);
  }
  biasPresentation(rack, "OwnedRackPresentation", [0.62, 0], 0.82);
  rack.position.x = 0.12;
  scene.add(rack);
  return scene;
}

function createRentedScene() {
  const scene = namedScene("RentedGPU");
  const assembly = new Group();
  assembly.name = "RentedGPUAssembly";
  addMesh(assembly, rounded(3.22, 0.14, 0.92, 0.055), MATERIALS.polymer, "Rented_GPU_Sled", [0, 0.07, 0]);
  const fanGeometry = new CylinderGeometry(0.135, 0.135, 0.055, 24);
  const fanHubGeometry = new CylinderGeometry(0.032, 0.032, 0.062, 14);
  for (const [index, y] of [0.39, 0.86].entries()) {
    const suffix = index === 0 ? "A" : "B";
    addMesh(assembly, rounded(3.04, 0.38, 0.82, 0.065), MATERIALS.graphite, `Rented_GPU_Chassis_${suffix}`, [0, y, 0]);
    for (let fan = 0; fan < 4; fan += 1) {
      const x = -1.02 + fan * 0.68;
      addMesh(
        assembly,
        fanGeometry,
        MATERIALS.polymer,
        `Rented_GPU_Chassis_${suffix}_Fan_${String(fan + 1).padStart(2, "0")}`,
        [x, y, 0.438],
        [Math.PI / 2, 0, 0],
      );
      addMesh(
        assembly,
        fanHubGeometry,
        MATERIALS.graphite,
        `Rented_GPU_Chassis_${suffix}_FanHub_${String(fan + 1).padStart(2, "0")}`,
        [x, y, 0.47],
        [Math.PI / 2, 0, 0],
      );
    }
    addMesh(assembly, new BoxGeometry(2.56, 0.045, 0.045), MATERIALS.orange, `Rented_GPU_Chassis_${suffix}_PowerRail`, [0, y + 0.145, 0.438]);
  }
  addMesh(assembly, rounded(0.72, 0.16, 0.045, 0.025, 2), MATERIALS.polymer, "Rented_GPU_ProviderPlate", [0, 1.11, 0.438]);
  addExtrudedBrace(assembly, "Rented_GPU_Interconnect", [-0.4, 1.22, -0.05], 1);
  scene.add(assembly);
  return scene;
}

function createCloudScene() {
  const scene = namedScene("CloudHPC");
  const bank = new Group();
  bank.name = "CloudRackBank";
  const bayGeometry = rounded(0.72, 0.155, 0.105, 0.022, 2);
  const railGeometry = rounded(0.055, 2.12, 0.065, 0.018, 2);
  for (const [index, x] of [-1.32, 0, 1.32].entries()) {
    const suffix = ["A", "B", "C"][index];
    const rack = new Group();
    rack.name = `CloudRack${suffix}Assembly`;
    addMesh(rack, rounded(0.86, 2.38, 0.72, 0.065), MATERIALS.graphite, `Cloud_Rack_${suffix}`, [x, 1.19, 0]);
    addMesh(rack, railGeometry, MATERIALS.infrastructureFace, `Cloud_Rack_${suffix}_LeftRail`, [x - 0.36, 1.19, 0.4]);
    addMesh(rack, railGeometry, MATERIALS.infrastructureFace, `Cloud_Rack_${suffix}_RightRail`, [x + 0.36, 1.19, 0.4]);
    for (let row = 0; row < 8; row += 1) {
      const y = 0.36 + row * 0.215;
      addMesh(rack, bayGeometry, MATERIALS.infrastructureFace, `Cloud_Rack_${suffix}_Bay_${String(row + 1).padStart(2, "0")}`, [x, y, 0.4]);
      addMesh(rack, new CylinderGeometry(0.018, 0.018, 0.025, 10), row === 7 ? MATERIALS.green : MATERIALS.orange, `Cloud_Rack_${suffix}_LED_${String(row + 1).padStart(2, "0")}`, [x + 0.35, y, 0.47], [Math.PI / 2, 0, 0]);
    }
    addMesh(rack, new BoxGeometry(0.68, 0.055, 0.05), MATERIALS.orange, `Cloud_Rack_${suffix}_FabricBus`, [x, 2.14, 0.405]);
    biasPresentation(rack, `CloudRack${suffix}Presentation`, [x, 0], 0.37);
    bank.add(rack);
  }
  addMesh(bank, rounded(3.78, 0.12, 0.16, 0.04, 2), MATERIALS.infrastructureFace, "Cloud_HPC_TopologySpine", [0, 2.5, 0.04]);
  scene.add(bank);
  return scene;
}

function createControlPlaneScene() {
  const scene = namedScene("FabricControlPlane");
  addMesh(
    scene,
    rounded(1.7, 0.86, 1.16, 0.14, 4),
    MATERIALS.graphite,
    "FabricControlPlane_Chassis",
    [0, 0.43, 0],
  );
  addMesh(scene, rounded(1.34, 0.48, 0.06, 0.08, 3), MATERIALS.glass, "FabricControlPlane_DisplayGlass", [0, 0.48, 0.61]);
  addMesh(scene, new BoxGeometry(1.18, 0.34, 0.025), MATERIALS.screen, "FabricControlPlane_DisplayRecess", [0, 0.48, 0.652]);
  const portGeometry = rounded(0.19, 0.11, 0.07, 0.025, 2);
  for (let index = 0; index < 4; index += 1) {
    const x = -0.54 + index * 0.36;
    addMesh(scene, portGeometry, MATERIALS.polymer, `FabricControlPlane_Port_${index + 1}`, [x, 0.2, 0.625]);
    addMesh(scene, new CylinderGeometry(0.018, 0.018, 0.025, 10), index === 3 ? MATERIALS.green : MATERIALS.orange, `FabricControlPlane_PortLED_${index + 1}`, [x, 0.2, 0.672], [Math.PI / 2, 0, 0]);
  }
  addExtrudedBrace(scene, "FabricControlPlane_ZolliRail", [-0.4, 0.93, -0.03], 1);
  return scene;
}

function namedScene(name) {
  const scene = new Scene();
  scene.name = name;
  return scene;
}

function parseOutputArgument(argv) {
  const index = argv.indexOf("--output");
  if (index === -1) return DEFAULT_OUTPUT;
  if (!argv[index + 1]) throw new Error("--output requires a directory");
  return resolve(process.cwd(), argv[index + 1]);
}

function cleanNumber(value) {
  return Number(value.toFixed(6));
}

function sceneBounds(scene) {
  scene.updateMatrixWorld(true);
  const bounds = new Box3().setFromObject(scene);
  return {
    min: bounds.min.toArray().map(cleanNumber),
    max: bounds.max.toArray().map(cleanNumber),
  };
}

function collectSemanticBounds(scene, semantics) {
  scene.updateMatrixWorld(true);
  return Object.fromEntries(
    semantics.map((semantic) => {
      const object = scene.getObjectByName(semantic);
      if (!object) {
        throw new Error(`Missing semantic group ${semantic} in ${scene.name}`);
      }
      const bounds = new Box3().setFromObject(object);
      return [
        semantic,
        {
          min: bounds.min.toArray().map(cleanNumber),
          max: bounds.max.toArray().map(cleanNumber),
        },
      ];
    }),
  );
}

async function exportBinary(scene) {
  const exporter = new GLTFExporter();
  const output = await exporter.parseAsync(scene, {
    binary: true,
    onlyVisible: true,
    trs: false,
  });
  return Buffer.from(output);
}

function optimize(input, output) {
  const result = spawnSync(
    GLTF_TRANSFORM,
    [
      "optimize",
      input,
      output,
      "--compress",
      "meshopt",
      "--flatten",
      "false",
      "--instance",
      "false",
      "--join",
      "false",
      "--palette",
      "false",
      "--simplify",
      "false",
      "--texture-compress",
      "auto",
      "--texture-size",
      "1024",
    ],
    { cwd: WEB_ROOT, encoding: "utf8" },
  );
  if (result.stdout) process.stdout.write(result.stdout);
  if (result.stderr) process.stderr.write(result.stderr);
  if (result.status !== 0) {
    throw new Error(`gltf-transform optimize failed for ${input}`);
  }
}

async function main() {
  const outputDirectory = parseOutputArgument(process.argv.slice(2));
  await mkdir(outputDirectory, { recursive: true });
  if (outputDirectory !== DEFAULT_OUTPUT) {
    await copyFile(join(DEFAULT_OUTPUT, "LICENSES.md"), join(outputDirectory, "LICENSES.md"));
  }
  const factories = {
    everyday: createEverydayScene,
    owned: createOwnedScene,
    rented: createRentedScene,
    cloud: createCloudScene,
    controlPlane: createControlPlaneScene,
  };
  const assets = [];

  for (const required of REQUIRED_FABRIC_ASSETS) {
    const scene = factories[required.key]();
    sceneBounds(scene);
    collectSemanticBounds(
      scene,
      required.requiredSemanticBounds,
    );
    const rawPath = join(outputDirectory, `.${required.file}.raw.glb`);
    const outputPath = join(outputDirectory, required.file);
    await writeFile(rawPath, await exportBinary(scene));
    optimize(rawPath, outputPath);
    await rm(rawPath, { force: true });

    const buffer = await readFile(outputPath);
    const inspected = await inspectFabricGlb(
      buffer,
      required.requiredSemanticBounds,
    );
    const semanticBounds = Object.fromEntries(
      required.requiredSemanticBounds.map((semantic) => {
        const inspectedBounds = inspected.semanticBounds[semantic];
        if (!inspectedBounds) {
          throw new Error(`Optimized ${required.file} is missing semantic bounds for ${semantic}`);
        }
        return [semantic, inspectedBounds];
      }),
    );
    assets.push({
      key: required.key,
      file: required.file,
      ownership: "first-party",
      byteLength: buffer.byteLength,
      triangleCount: inspected.triangleCount,
      meshNames: inspected.meshNames,
      boundingBox: inspected.boundingBox,
      semanticBounds,
      forward: [0, 0, 1],
      sha256: createHash("sha256").update(buffer).digest("hex"),
    });
    process.stdout.write(
      `Generated ${required.file}: ${buffer.byteLength} bytes, ${inspected.triangleCount} triangles\n`,
    );
  }

  const manifest = {
    schemaVersion: 2,
    generator: "scripts/hero-assets/build-fabric-assets.mjs",
    licenseFile: "LICENSES.md",
    optimization: {
      tool: "@gltf-transform/cli",
      compression: "meshopt",
      textureMaxDimension: 1024,
    },
    totalByteLength: assets.reduce((total, asset) => total + asset.byteLength, 0),
    assets,
  };
  await writeFile(
    join(outputDirectory, "asset-manifest.json"),
    `${JSON.stringify(manifest, null, 2)}\n`,
  );
  process.stdout.write(
    `Wrote ${join(outputDirectory, "asset-manifest.json")} (${manifest.totalByteLength} GLB bytes total)\n`,
  );
}

await main();
