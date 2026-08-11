const freezeArray = (values) => Object.freeze(values);

const rentedChassisMeshes = freezeArray([
  "Rented_GPU_Chassis_A",
  "Rented_GPU_Chassis_B",
  "Rented_GPU_Chassis_A_Fan_01",
  "Rented_GPU_Chassis_A_Fan_02",
  "Rented_GPU_Chassis_A_Fan_03",
  "Rented_GPU_Chassis_B_Fan_01",
  "Rented_GPU_Chassis_B_Fan_02",
  "Rented_GPU_Chassis_B_Fan_03",
]);

const cloudRackMeshes = (rack) => freezeArray([
  `${rack}_LeftRail`,
  `${rack}_RightRail`,
  `${rack}_Bay_01`,
  `${rack}_Bay_02`,
  `${rack}_Bay_03`,
  `${rack}_Bay_04`,
  `${rack}_Bay_05`,
  `${rack}_Bay_06`,
  `${rack}_Bay_07`,
  `${rack}_Bay_08`,
]);

export const FABRIC_ASSET_SILHOUETTES = Object.freeze({
  everyday: Object.freeze({
    requiredMeshes: freezeArray([
      "Everyday_Laptop",
      "Everyday_Workstation",
      "Everyday_Tower",
      "Everyday_HomeServer",
    ]),
    requiredSemanticBounds: freezeArray([
      "EverydayLaptopAssembly",
      "EverydayWorkstationAssembly",
      "EverydayTowerAssembly",
      "EverydayHomeServerAssembly",
    ]),
    ratios: freezeArray([]),
    relativeRatios: freezeArray([]),
  }),
  owned: Object.freeze({
    requiredMeshes: freezeArray([
      "Owned_Workstation",
      "Owned_Rack",
      "Owned_Workstation_GPU_Bay_01",
      "Owned_Workstation_GPU_Bay_02",
      "Owned_Workstation_Fan_01",
      "Owned_Workstation_Fan_02",
      "Owned_Rack_LeftRail",
      "Owned_Rack_RightRail",
      "Owned_Rack_Bay_01",
      "Owned_Rack_Bay_02",
      "Owned_Rack_Bay_03",
      "Owned_Rack_Bay_04",
      "Owned_Rack_Bay_05",
      "Owned_Rack_Bay_06",
    ]),
    requiredSemanticBounds: freezeArray([
      "OwnedWorkstationAssembly",
      "OwnedRackAssembly",
    ]),
    ratios: freezeArray([]),
    relativeRatios: freezeArray([]),
  }),
  rented: Object.freeze({
    requiredMeshes: freezeArray([
      "Rented_GPU_Sled",
      ...rentedChassisMeshes,
      "Rented_GPU_ProviderPlate",
      "Rented_GPU_Interconnect",
    ]),
    requiredSemanticBounds: freezeArray(["RentedGPUAssembly"]),
    ratios: freezeArray([
      Object.freeze({
        numerator: freezeArray(["RentedGPUAssembly", "x"]),
        denominator: freezeArray(["RentedGPUAssembly", "y"]),
        min: 2.2,
      }),
    ]),
    relativeRatios: freezeArray([]),
  }),
  cloud: Object.freeze({
    requiredMeshes: freezeArray([
      ...cloudRackMeshes("Cloud_Rack_A"),
      ...cloudRackMeshes("Cloud_Rack_B"),
      ...cloudRackMeshes("Cloud_Rack_C"),
      "Cloud_HPC_TopologySpine",
    ]),
    requiredSemanticBounds: freezeArray([
      "CloudRackBank",
      "CloudRackAAssembly",
      "CloudRackBAssembly",
      "CloudRackCAssembly",
    ]),
    ratios: freezeArray([]),
    relativeRatios: freezeArray([
      Object.freeze({
        subject: freezeArray(["CloudRackBank", "y"]),
        reference: freezeArray(["owned", "OwnedRackAssembly", "y"]),
        min: 1.35,
      }),
      Object.freeze({
        subject: freezeArray(["CloudRackBank", "x"]),
        reference: freezeArray(["owned", "OwnedRackAssembly", "x"]),
        min: 1.25,
      }),
    ]),
  }),
  controlPlane: Object.freeze({
    requiredMeshes: freezeArray([
      "FabricControlPlane_Chassis",
      "FabricControlPlane_DisplayGlass",
      "FabricControlPlane_DisplayRecess",
      "FabricControlPlane_Port_1",
      "FabricControlPlane_Port_2",
      "FabricControlPlane_Port_3",
      "FabricControlPlane_Port_4",
    ]),
    requiredSemanticBounds: freezeArray(["FabricControlPlane_Chassis"]),
    ratios: freezeArray([]),
    relativeRatios: freezeArray([]),
  }),
});
