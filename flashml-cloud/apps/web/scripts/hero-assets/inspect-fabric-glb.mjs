import { getBounds, NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { MeshoptDecoder } from "meshoptimizer";

const IO = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ "meshopt.decoder": MeshoptDecoder });

function cleanNumber(value) {
  return Number(value.toFixed(6));
}

function cleanArray(values) {
  return Array.from(values, cleanNumber);
}

function cleanBounds(bounds) {
  return {
    min: cleanArray(bounds.min),
    max: cleanArray(bounds.max),
  };
}

function forwardFromMatrix(matrix) {
  const forward = [matrix[8], matrix[9], matrix[10]];
  const length = Math.hypot(...forward);
  if (!Number.isFinite(length) || length === 0) return null;
  return forward.map((value) => cleanNumber(value / length));
}

function triangleCountForNode(node) {
  const mesh = node.getMesh();
  if (!mesh) return 0;
  return mesh.listPrimitives().reduce((total, primitive) => {
    const elements = primitive.getIndices() ?? primitive.getAttribute("POSITION");
    return total + Math.floor((elements?.getCount() ?? 0) / 3);
  }, 0);
}

export async function inspectFabricGlb(bytes, semanticNames) {
  await MeshoptDecoder.ready;
  const document = await IO.readBinary(bytes);
  const root = document.getRoot();
  const scene = root.listScenes()[0];
  if (!scene) throw new Error("Fabric GLB does not contain a scene");

  const sceneNodes = [];
  for (const child of scene.listChildren()) {
    child.traverse((node) => sceneNodes.push(node));
  }
  const namedNodes = sceneNodes.filter((node) => node.getName());
  const nodeNames = namedNodes.map((node) => node.getName()).sort();
  const meshNames = namedNodes
    .filter((node) => node.getMesh())
    .map((node) => node.getName())
    .sort();
  const meshMaterials = Object.fromEntries(
    namedNodes.flatMap((node) => {
      const mesh = node.getMesh();
      if (!mesh) return [];
      const names = new Set(
        mesh.listPrimitives()
          .map((primitive) => primitive.getMaterial()?.getName())
          .filter(Boolean),
      );
      return names.size === 1 ? [[node.getName(), [...names][0]]] : [];
    }),
  );
  const triangleCount = sceneNodes
    .reduce((total, node) => total + triangleCountForNode(node), 0);
  const materials = root.listMaterials()
    .map((material) => ({
      name: material.getName(),
      baseColor: cleanArray(material.getBaseColorFactor()),
      metalness: cleanNumber(material.getMetallicFactor()),
      roughness: cleanNumber(material.getRoughnessFactor()),
    }))
    .sort((left, right) => left.name.localeCompare(right.name));
  const nodeTransforms = namedNodes
    .map((node) => {
      const worldMatrix = node.getWorldMatrix();
      return {
        name: node.getName(),
        worldMatrix: cleanArray(worldMatrix),
        forward: forwardFromMatrix(worldMatrix),
      };
    })
    .sort((left, right) => left.name.localeCompare(right.name));

  const semanticBounds = {};
  const semanticForwards = {};
  for (const semantic of semanticNames) {
    const matches = namedNodes.filter((node) => node.getName() === semantic);
    if (matches.length !== 1) continue;
    semanticBounds[semantic] = cleanBounds(getBounds(matches[0]));
    semanticForwards[semantic] = forwardFromMatrix(matches[0].getWorldMatrix());
  }

  const firstSemanticForward = semanticNames
    .map((semantic) => semanticForwards[semantic])
    .find(Boolean);

  return {
    nodeNames,
    meshNames,
    triangleCount,
    boundingBox: cleanBounds(getBounds(scene)),
    semanticBounds,
    semanticForwards,
    forward: firstSemanticForward ?? null,
    nodeTransforms,
    materials,
    meshMaterials,
  };
}
