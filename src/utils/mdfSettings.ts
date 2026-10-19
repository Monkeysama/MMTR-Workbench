// MDF45 field names and bit positions, cross-checked with the RE Mesh MDF UI.
export const shaderTypes = [
  "Standard", "Decal", "DecalStencil", "SeparateAlphaDecal", "DecalWithMetallic", "DecalNRMR",
  "Transparent", "TransparentStencil", "Distortion", "PrimitiveMesh", "PrimitiveSolidMesh", "Water",
  "SpeedTree", "GUI", "GUIMesh", "GUIMeshTransparent", "ExpensiveTransparent", "Forward", "RenderTarget",
  "PostProcess", "PrimitiveMaterial", "PrimitiveSolidMaterial", "PrimitiveSolidMaterialExpensive",
  "SpineMaterial", "VolumetricFog", "ShellFurMaterial", "VolumeDecal", "AlembicMesh", "AlembicMeshForward",
  "AlembicMeshTransparent", "MarchingCubes", "MarchingCubesForward", "MarchingCubesTransparent", "Strands",
  "Eyeball", "EyeballPostProcess", "NFXTransparent", "Cloudscape2", "CloudscapeReserved", "VolumeSolidMaterial", "VolumeDecalMetallic",
];
export interface MdfFlagField { name: string; bit: number; width: number }
export const flagFields: MdfFlagField[][] = [
  [
    ...["BaseTwoSideEnable", "BaseAlphaTestEnable", "ShadowCastDisable", "VertexShaderUsed", "EmissiveUsed",
      "TessellationEnable", "EnableIgnoreDepth", "AlphaMaskUsed", "ForcedTwoSideEnable", "TwoSideEnable",
      "TransparentZPostPassEnable"].map((name, bit) => ({ name, bit, width: 1 })),
    { name: "TessFactor", bit: 11, width: 5 }, { name: "PhongFactor", bit: 16, width: 8 },
    ...["RoughTransparentEnable", "ForcedAlphaTestEnable", "AlphaTestEnable", "SSSProfileUsed", "EnableStencilPriority",
      "RequireDualQuaternion", "PixelDepthOffsetUsed", "NoRayTracing"].map((name, i) => ({ name, bit: 24 + i, width: 1 })),
  ],
  [
    ...["TransparentDistortionEnable", "AlphaUsed", "BakeTextureUseSecondaryUV", "ForwardPrepassEnabled",
      "ForcedAlphaTestEnableShadow", "TessellationZPrepassDisable", "DitheredLodTransitionEnable", "reserved0"].map((name, bit) => ({ name, bit, width: 1 })),
    { name: "TransparentPriorityBias", bit: 8, width: 8 }, { name: "reserved1", bit: 16, width: 8 }, { name: "reserved2", bit: 24, width: 8 },
  ],
];
export function flagValue(value: number, field: MdfFlagField) { return Math.floor(value / 2 ** field.bit) % 2 ** field.width; }
export function replaceFlag(value: number, field: MdfFlagField, next: number) { return value + (next - flagValue(value, field)) * 2 ** field.bit; }
