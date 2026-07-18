"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Spinner,
  CheckCircle,
  ArrowRight,
  Cpu,
  HardDrive,
  Lightning,
  Eye,
  TreeStructure,
  Brain,
} from "@phosphor-icons/react";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";
import {
  MODEL_REGISTRY,
  CATEGORIES,
  PATTERN_LABELS,
  PATTERN_COLORS,
  PATTERN_SHORT,
  TOPOLOGY_FLOWS,
  patternToArch,
  getModelsByCategory,
  getModelById,
  type MLModel,
} from "@/lib/models";
import {
  startTraining,
  uploadDataset,
  JOB_ID_STORAGE_KEY,
  type UploadedDataset,
  type NodeType,
} from "@/lib/api";

const NODE_TYPES: { id: NodeType; label: string; spec: string; cost: string; active: boolean }[] = [
  {
    id: "cpu_small",
    label: "CPU Small",
    spec: "1 vCPU / 2GB — CpuInstanceType.CPU3C_1_2",
    cost: "cheapest",
    active: true,
  },
  {
    id: "cpu_large",
    label: "CPU Large",
    spec: "4 vCPU / 8GB — CpuInstanceType.CPU5C_4_8",
    cost: "faster",
    active: true,
  },
  {
    id: "gpu_any",
    label: "GPU Any",
    spec: "Lowest available GPU — GpuGroup.ANY",
    cost: "gpu",
    active: true,
  },
  {
    id: "gpu_ada_24",
    label: "NVIDIA RTX 4090",
    spec: "24GB VRAM — GpuGroup.ADA_24",
    cost: "4090",
    active: true,
  },
  {
    id: "gpu_ada_32_pro",
    label: "NVIDIA RTX 5090",
    spec: "32GB VRAM — GpuGroup.ADA_32_PRO",
    cost: "5090",
    active: true,
  },
  {
    id: "gpu_ada_80_pro",
    label: "NVIDIA H100",
    spec: "80GB+ VRAM — GpuGroup.ADA_80_PRO",
    cost: "H100",
    active: true,
  },
  {
    id: "gpu_ampere_24",
    label: "GPU Ampere 24GB",
    spec: "RTX A5000 / L4 / RTX 3090 — GpuGroup.AMPERE_24",
    cost: "24GB",
    active: true,
  },
];

function ModelCard({
  model,
  selected,
  onClick,
}: {
  model: MLModel;
  selected: boolean;
  onClick: () => void;
}) {
  const colors = PATTERN_COLORS[model.executionPattern];
  return (
    <button
      onClick={onClick}
      className={cn(
        "text-left p-3 rounded-lg border transition-all w-full",
        selected
          ? cn(colors.bg, colors.border, "border")
          : "border-border/40 bg-transparent hover:border-border/70"
      )}
    >
      <div className="flex items-start justify-between gap-1 mb-1.5">
        <span
          className={cn(
            "text-sm font-semibold leading-tight",
            selected ? "text-foreground" : "text-foreground/80"
          )}
        >
          {model.name}
        </span>
        {model.status === "visualization" && (
          <span className="text-[9px] font-mono px-1 py-0.5 rounded border border-border/40 text-muted-foreground shrink-0 mt-0.5">
            PREVIEW
          </span>
        )}
      </div>
      <span
        className={cn(
          "inline-block text-[9px] font-mono px-1.5 py-0.5 rounded border mb-1.5",
          colors.text,
          colors.bg,
          colors.border
        )}
      >
        {PATTERN_SHORT[model.executionPattern]}
      </span>
      <p className="text-[11px] text-muted-foreground leading-relaxed line-clamp-2">
        {model.description}
      </p>
    </button>
  );
}

export function LaunchConfig() {
  const router = useRouter();

  const [selectedModelId, setSelectedModelId] = useState("kmeans");
  const [nodeType, setNodeType] = useState<NodeType>("cpu_small");
  const [workers, setWorkers] = useState([3]);
  const [clusters, setClusters] = useState([3]);
  const [maxIter, setMaxIter] = useState([15]);
  const [maxEpochs, setMaxEpochs] = useState([20]);
  const [nPoints, setNPoints] = useState([1000]);
  const [nTrees, setNTrees] = useState([30]);

  const [uploadedDataset, setUploadedDataset] = useState<UploadedDataset | null>(null);
  const [uploadingDataset, setUploadingDataset] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [launching, setLaunching] = useState(false);
  const [launchError, setLaunchError] = useState<string | null>(null);

  const model = getModelById(selectedModelId) ?? MODEL_REGISTRY[0];
  const arch = patternToArch(model.executionPattern);
  const colors = PATTERN_COLORS[model.executionPattern];
  const canTrain = model.status === "implemented" && arch !== null;
  const needsUpload = model.supportsDatasetUpload;
  const canLaunch =
    canTrain && !launching && !uploadingDataset && (!needsUpload || uploadedDataset !== null);

  const handleSelectModel = (id: string) => {
    setSelectedModelId(id);
    if (!getModelById(id)?.supportsDatasetUpload) {
      setUploadedDataset(null);
      setUploadError(null);
    }
  };

  const handleDatasetUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setUploadingDataset(true);
    setUploadError(null);
    setUploadedDataset(null);
    try {
      const uploaded = await uploadDataset(file);
      setUploadedDataset(uploaded);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Failed to upload dataset");
    } finally {
      setUploadingDataset(false);
    }
  };

  const handleLaunch = async () => {
    if (!arch) return;
    if (needsUpload && !uploadedDataset) {
      setLaunchError("Upload a CSV dataset before launching.");
      return;
    }
    setLaunching(true);
    setLaunchError(null);
    try {
      const { job_id } = await startTraining({
        model_id: model.id,
        architecture: arch,
        algorithm: model.algorithm,
        dataset: needsUpload ? uploadedDataset!.dataset : "sklearn",
        k: clusters[0],
        workers: workers[0],
        max_iter: maxIter[0],
        node_type: nodeType,
        n_points: nPoints[0],
        learning_rate: 0.05,
        max_epochs: maxEpochs[0],
        n_trees: nTrees[0],
      });
      localStorage.setItem(JOB_ID_STORAGE_KEY, job_id);
      router.push("/dashboard");
    } catch (err) {
      setLaunchError(
        err instanceof Error
          ? err.message
          : "Failed to reach the FlashML coordinator. Is it running on :8000?"
      );
      setLaunching(false);
    }
  };

  const flowSteps = TOPOLOGY_FLOWS[model.executionPattern];

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 py-10">
      <div className="mb-8">
        <h1 className="text-2xl font-bold tracking-tight text-foreground mb-2">
          Select a Model
        </h1>
        <p className="text-muted-foreground text-sm">
          FlashML automatically determines the distributed execution architecture. You just choose what to train.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 items-start">

        {/* Model browser — left 3 columns */}
        <div className="lg:col-span-3 order-2 lg:order-1 space-y-6">
          {CATEGORIES.map((category) => {
            const models = getModelsByCategory(category);
            if (models.length === 0) return null;
            const implemented = models.filter((m) => m.status === "implemented").length;
            return (
              <div key={category}>
                <div className="flex items-center gap-3 mb-3">
                  <span className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
                    {category}
                  </span>
                  <div className="flex-1 h-px bg-border/40" />
                  <span className="text-[10px] font-mono text-muted-foreground/50">
                    {implemented}/{models.length} implemented
                  </span>
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
                  {models.map((m) => (
                    <ModelCard
                      key={m.id}
                      model={m}
                      selected={selectedModelId === m.id}
                      onClick={() => handleSelectModel(m.id)}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Config panel — right 2 columns, sticky */}
        <div className="lg:col-span-2 order-1 lg:order-2 space-y-5 lg:sticky lg:top-6">

          {/* Selected model info */}
          <div className={cn("p-5 rounded-lg border", colors.border, colors.bg)}>
            <div className="flex items-start justify-between gap-3 mb-3">
              <div>
                <div className="flex items-center gap-2 mb-1">
                  <h2 className="text-base font-bold text-foreground">{model.name}</h2>
                  {model.status === "visualization" ? (
                    <span className="flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded border border-amber-400/40 bg-amber-400/10 text-amber-400">
                      <Eye className="w-2.5 h-2.5" weight="bold" />
                      PREVIEW
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded border border-node-green/40 bg-node-green/10 text-node-green">
                      <CheckCircle className="w-2.5 h-2.5" weight="bold" />
                      READY
                    </span>
                  )}
                </div>
                <span
                  className={cn(
                    "inline-block text-[10px] font-mono px-1.5 py-0.5 rounded border",
                    colors.text, colors.border, "bg-transparent"
                  )}
                >
                  {PATTERN_LABELS[model.executionPattern]}
                </span>
              </div>
              <Brain className={cn("w-5 h-5 shrink-0 mt-0.5", colors.text)} weight="duotone" />
            </div>

            <p className="text-xs text-muted-foreground leading-relaxed mb-3">
              {model.whyDistributed}
            </p>

            <div className="text-[10px] font-mono text-muted-foreground/60 border-t border-border/30 pt-3">
              {model.distributedStrategy}
            </div>

            {model.status === "visualization" && (
              <div className="mt-3 flex items-start gap-1.5 text-[10px] font-mono text-amber-400">
                <Eye className="w-3 h-3 shrink-0 mt-0.5" weight="bold" />
                Visualization preview only — backend implementation coming soon.
              </div>
            )}
          </div>

          {/* Node type */}
          <div className="p-4 rounded-lg border border-border/60 bg-surface">
            <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-2">
              <Cpu className="w-3.5 h-3.5 text-cyan" weight="duotone" />
              Node Type
            </h3>
            <div className="space-y-1.5">
              {NODE_TYPES.map((nt) => {
                const sel = nodeType === nt.id;
                return (
                  <button
                    key={nt.id}
                    disabled={!nt.active}
                    onClick={() => nt.active && setNodeType(nt.id)}
                    className={cn(
                      "w-full text-left p-2.5 rounded-lg border transition-all text-xs",
                      sel
                        ? "border-cyan/40 bg-cyan/8"
                        : nt.active
                        ? "border-border/40 hover:border-border/70"
                        : "border-border/20 opacity-40 cursor-not-allowed"
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className={cn("font-medium", sel ? "text-foreground" : "text-foreground/80")}>
                        {nt.label}
                      </span>
                      <span className="text-[10px] font-mono text-muted-foreground">{nt.cost}</span>
                    </div>
                    <div className="text-[10px] font-mono text-muted-foreground mt-0.5">{nt.spec}</div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Execution config */}
          <div className="p-4 rounded-lg border border-border/60 bg-surface space-y-4">
            <h3 className="text-xs font-semibold text-foreground flex items-center gap-2">
              <Lightning className="w-3.5 h-3.5 text-cyan" weight="duotone" />
              Execution Config
            </h3>

            <div>
              <div className="flex justify-between items-center mb-2">
                <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">
                  Workers (Flash nodes)
                </span>
                <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>
                  {workers[0]}
                </span>
              </div>
              <Slider
                value={workers}
                onValueChange={(v) => setWorkers(Array.isArray(v) ? (v as number[]) : [v as number])}
                min={1} max={3} step={1} className="w-full"
              />
            </div>

            {model.id === "kmeans" && (
              <>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Clusters (k)</span>
                    <span className="text-sm font-mono font-bold text-cyan metric-value">{clusters[0]}</span>
                  </div>
                  <Slider value={clusters} onValueChange={(v) => setClusters(Array.isArray(v) ? (v as number[]) : [v as number])} min={2} max={8} step={1} className="w-full" />
                </div>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Max Iterations</span>
                    <span className="text-sm font-mono font-bold text-cyan metric-value">{maxIter[0]}</span>
                  </div>
                  <Slider value={maxIter} onValueChange={(v) => setMaxIter(Array.isArray(v) ? (v as number[]) : [v as number])} min={5} max={30} step={1} className="w-full" />
                </div>
              </>
            )}

            {(model.id === "linear_regression" || model.id === "logistic_regression") && (
              <>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Max Epochs</span>
                    <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>{maxEpochs[0]}</span>
                  </div>
                  <Slider value={maxEpochs} onValueChange={(v) => setMaxEpochs(Array.isArray(v) ? (v as number[]) : [v as number])} min={5} max={50} step={5} className="w-full" />
                </div>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Dataset Size</span>
                    <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>{nPoints[0].toLocaleString()}</span>
                  </div>
                  <Slider value={nPoints} onValueChange={(v) => setNPoints(Array.isArray(v) ? (v as number[]) : [v as number])} min={200} max={5000} step={200} className="w-full" />
                </div>
              </>
            )}

            {model.id === "naive_bayes" && (
              <div>
                <div className="flex justify-between items-center mb-2">
                  <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Dataset Size</span>
                  <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>{nPoints[0].toLocaleString()}</span>
                </div>
                <Slider value={nPoints} onValueChange={(v) => setNPoints(Array.isArray(v) ? (v as number[]) : [v as number])} min={200} max={5000} step={200} className="w-full" />
                <p className="text-[10px] font-mono text-muted-foreground/60 mt-2">
                  Single-pass MapReduce — workers compute class statistics in parallel
                </p>
              </div>
            )}

            {model.id === "random_forest" && (
              <>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Total Trees</span>
                    <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>{nTrees[0]}</span>
                  </div>
                  <Slider value={nTrees} onValueChange={(v) => setNTrees(Array.isArray(v) ? (v as number[]) : [v as number])} min={10} max={60} step={10} className="w-full" />
                </div>
                <div>
                  <div className="flex justify-between items-center mb-2">
                    <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wider">Dataset Size</span>
                    <span className={cn("text-sm font-mono font-bold metric-value", colors.text)}>{nPoints[0].toLocaleString()}</span>
                  </div>
                  <Slider value={nPoints} onValueChange={(v) => setNPoints(Array.isArray(v) ? (v as number[]) : [v as number])} min={200} max={3000} step={200} className="w-full" />
                </div>
                <p className="text-[10px] font-mono text-muted-foreground/60">
                  {nTrees[0]} trees across {workers[0]} worker{workers[0] > 1 ? "s" : ""} ({Math.ceil(nTrees[0] / workers[0])} per worker)
                </p>
              </>
            )}

            {model.id === "hyperparameter_search" && (
              <p className="text-[10px] font-mono text-muted-foreground p-3 rounded border border-border/40 bg-surface-elevated leading-relaxed">
                Config space: degree ∈ [1,2,3] × Ridge α ∈ [0.01, 1.0, 100] = 9 configs
                <br />Assigned round-robin to {workers[0]} worker{workers[0] > 1 ? "s" : ""}
              </p>
            )}

            {model.status === "visualization" && (
              <p className="text-[10px] font-mono text-muted-foreground/60 p-3 rounded border border-border/40 bg-surface-elevated">
                Configuration will be enabled when the backend is implemented.
              </p>
            )}
          </div>

          {/* Dataset */}
          <div className="p-4 rounded-lg border border-border/60 bg-surface">
            <h3 className="text-xs font-semibold text-foreground mb-3 flex items-center gap-2">
              <HardDrive className="w-3.5 h-3.5 text-cyan" weight="duotone" />
              Dataset
            </h3>
            {needsUpload ? (
              <>
                <label
                  className={cn(
                    "flex min-h-28 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed p-4 text-center transition-all",
                    uploadedDataset ? "border-node-green/50 bg-node-green/5" : "border-border/50 hover:border-cyan/40 hover:bg-cyan/5"
                  )}
                >
                  <input type="file" className="hidden" accept=".csv,text/csv" onChange={handleDatasetUpload} disabled={uploadingDataset || launching} />
                  {uploadingDataset ? (
                    <div className="flex flex-col items-center gap-2">
                      <Spinner className="h-5 w-5 animate-spin text-cyan" weight="bold" />
                      <span className="text-xs text-cyan">Uploading CSV...</span>
                    </div>
                  ) : uploadedDataset ? (
                    <div className="flex flex-col items-center gap-2">
                      <CheckCircle className="h-5 w-5 text-node-green" weight="fill" />
                      <span className="text-xs font-medium text-node-green">{uploadedDataset.filename}</span>
                      <span className="text-[10px] text-muted-foreground">{uploadedDataset.rows.toLocaleString()} rows</span>
                    </div>
                  ) : (
                    <div className="flex flex-col items-center gap-2">
                      <HardDrive className="h-5 w-5 text-muted-foreground" weight="duotone" />
                      <span className="text-xs text-muted-foreground">Choose CSV dataset</span>
                      <span className="text-[10px] text-muted-foreground/60">Required: issue_description column</span>
                    </div>
                  )}
                </label>
                {uploadError && <p className="mt-2 text-[11px] text-red-400">{uploadError}</p>}
              </>
            ) : (
              <div className="flex items-center gap-3 p-3 rounded-lg bg-surface-elevated border border-border/40">
                <CheckCircle className="w-4 h-4 text-node-green shrink-0" weight="fill" />
                <div>
                  <span className="text-xs text-foreground/80">scikit-learn example dataset — auto-loaded</span>
                  <div className="text-[10px] text-muted-foreground mt-0.5">
                    {model.id === "hyperparameter_search"
                      ? "load_diabetes · tabular regression"
                      : model.id === "naive_bayes"
                      ? "load_wine · tabular multiclass classification"
                      : model.id === "random_forest"
                      ? "load_breast_cancer · tabular binary classification"
                      : model.id === "logistic_regression"
                      ? "load_breast_cancer · tabular binary classification"
                      : model.id === "linear_regression"
                      ? "load_diabetes · tabular regression"
                      : "loaded by coordinator"}
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Launch */}
          <button
            onClick={handleLaunch}
            disabled={!canLaunch}
            className={cn(
              "w-full py-3 rounded-lg font-semibold text-sm flex items-center justify-center gap-2 transition-all",
              canLaunch
                ? cn("text-background hover:opacity-90 active:scale-[0.98]",
                    model.executionPattern === "MAP_REDUCE"              ? "bg-cyan glow-cyan"
                    : model.executionPattern === "GRADIENT_SYNC"         ? "bg-violet-500"
                    : "bg-node-green")
                : "bg-muted text-muted-foreground cursor-not-allowed"
            )}
          >
            {launching ? (
              <><Spinner className="w-4 h-4 animate-spin" weight="bold" /> Starting on Runpod Flash...</>
            ) : !canTrain ? (
              <><Eye className="w-4 h-4" weight="bold" /> Visualization Preview Only</>
            ) : (
              <>
                <TreeStructure className="w-4 h-4" weight="duotone" />
                Launch {model.name}
                <ArrowRight weight="bold" className="w-4 h-4" />
              </>
            )}
          </button>

          {launchError && <p className="text-[11px] text-red-400 text-center">{launchError}</p>}
          {needsUpload && !uploadedDataset && !uploadingDataset && canTrain && (
            <p className="text-[11px] text-muted-foreground text-center">
              Upload a CSV dataset to enable training.
            </p>
          )}

          {/* Topology preview */}
          <div className="p-4 rounded-lg border border-border/40 bg-surface/50">
            <div className="text-[10px] font-mono text-muted-foreground mb-3 uppercase tracking-wider">
              {PATTERN_LABELS[model.executionPattern]} topology
            </div>
            <div className="flex flex-col items-center gap-1.5">
              {flowSteps.map((step, i) => {
                const isWorker = step.toLowerCase().includes("worker");
                const stepCls =
                  i === 0
                    ? cn(colors.text, colors.bg, colors.border, "border")
                    : i === flowSteps.length - 1
                    ? "border-node-green/30 bg-node-green/8 text-node-green"
                    : "border-border/50 bg-surface text-muted-foreground";
                return (
                  <div key={step} className="flex flex-col items-center gap-1 w-full">
                    {i > 0 && (
                      <svg className="w-px h-3" viewBox="0 0 1 12">
                        <line x1="0.5" y1="0" x2="0.5" y2="12" stroke="currentColor" strokeWidth="1" className="text-border" />
                      </svg>
                    )}
                    {isWorker ? (
                      <div className="flex flex-wrap gap-1.5 justify-center">
                        {Array.from({ length: workers[0] }).map((_, wi) => (
                          <div key={wi} className={cn("px-2 py-1 rounded border text-[10px] font-mono", stepCls)}>
                            node-{wi}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className={cn("px-3 py-1.5 rounded border text-[11px] font-mono w-full text-center", stepCls)}>
                        {step}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
