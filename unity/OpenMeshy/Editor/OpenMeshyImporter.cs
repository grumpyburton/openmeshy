// OpenMeshy importer: sets up models exported by scripts/unity_export.py as they import.
//
// Each export is a folder holding NAME.fbx, NAME.openmeshy.json and textures/. When Unity
// imports an FBX that has a matching .openmeshy.json beside it, this:
//   * sets the rig: Humanoid (avatar created from the model) or Generic, as the manifest says;
//   * turns on Bake Axis Conversion, so the model stands up with no -90 degree rotation;
//   * flags normal maps as normal maps and data maps as linear;
//   * builds URP Lit materials (Standard on the built-in pipeline) from the textures.
// FBX files without a manifest are left exactly as Unity would import them.
//
// Install once per project at Assets/OpenMeshy/Editor/ (unity_export.py --unity-project
// does it). Never copy it into each export: two copies of one class will not compile.

using System;
using System.IO;
using UnityEditor;
using UnityEditor.AssetImporters;
using UnityEngine;

namespace OpenMeshy
{
    [Serializable]
    public class MaterialEntry
    {
        public string name;
        public float[] baseColorFactor;
        public float metallic;
        public float smoothness;
        public string baseColor;
        public string normal;
        public string metallicSmoothness;
        public string occlusion;
        public string emission;
    }

    [Serializable]
    public class Manifest
    {
        public int version;
        public string name;
        public string rig;
        public bool hasAnimations;
        public float appliedScale;
        public MaterialEntry[] materials;
    }

    public class OpenMeshyImporter : AssetPostprocessor
    {
        const string Suffix = ".openmeshy.json";

        public override uint GetVersion() => 2;

        static string ManifestPath(string modelPath) =>
            Path.Combine(Path.GetDirectoryName(modelPath) ?? "",
                         Path.GetFileNameWithoutExtension(modelPath) + Suffix).Replace('\\', '/');

        static Manifest Load(string modelPath)
        {
            var path = ManifestPath(modelPath);
            if (!File.Exists(path)) return null;
            try { return JsonUtility.FromJson<Manifest>(File.ReadAllText(path)); }
            catch (Exception e)
            {
                Debug.LogWarning($"OpenMeshy: could not read {path}: {e.Message}");
                return null;
            }
        }

        void OnPreprocessModel()
        {
            var manifest = Load(assetPath);
            if (manifest == null) return;
            context.DependsOnSourceAsset(ManifestPath(assetPath));

            var importer = (ModelImporter)assetImporter;
            importer.globalScale = 1f;
            importer.useFileScale = true;
            importer.bakeAxisConversion = true;
            importer.importAnimation = manifest.hasAnimations;
            importer.materialImportMode = ModelImporterMaterialImportMode.ImportViaMaterialDescription;
            switch (manifest.rig)
            {
                case "Humanoid":
                    importer.animationType = ModelImporterAnimationType.Human;
                    importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                    break;
                case "Generic":
                    importer.animationType = ModelImporterAnimationType.Generic;
                    importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
                    break;
                default:
                    importer.animationType = ModelImporterAnimationType.None;
                    break;
            }
        }

        void OnPreprocessTexture()
        {
            var folder = Path.GetDirectoryName(assetPath)?.Replace('\\', '/');
            if (folder == null || !folder.EndsWith("/textures")) return;
            var exportDir = Path.GetDirectoryName(folder);
            if (exportDir == null || Directory.GetFiles(exportDir, "*" + Suffix).Length == 0) return;

            var importer = (TextureImporter)assetImporter;
            var file = Path.GetFileNameWithoutExtension(assetPath);
            if (file.EndsWith("_normal"))
            {
                importer.textureType = TextureImporterType.NormalMap;
            }
            else if (file.EndsWith("_metallicSmoothness") || file.EndsWith("_occlusion"))
            {
                importer.sRGBTexture = false;
            }
        }

        void OnPreprocessMaterialDescription(MaterialDescription description, Material material,
                                             AnimationClip[] clips)
        {
            var manifest = Load(assetPath);
            if (manifest?.materials == null) return;
            var entry = Array.Find(manifest.materials, m => m.name == description.materialName)
                        ?? (manifest.materials.Length == 1 ? manifest.materials[0] : null);
            if (entry == null)
            {
                Debug.LogWarning($"OpenMeshy: {assetPath} has material '{description.materialName}', "
                                 + "which its manifest does not list; left as Unity made it.");
                return;
            }

            var urp = Shader.Find("Universal Render Pipeline/Lit");
            material.shader = urp != null ? urp : Shader.Find("Standard");
            bool isUrp = urp != null;
            var folder = Path.GetDirectoryName(assetPath)?.Replace('\\', '/') + "/textures/";

            Texture2D Tex(string file)
            {
                if (string.IsNullOrEmpty(file)) return null;
                context.DependsOnSourceAsset(folder + file);
                return AssetDatabase.LoadAssetAtPath<Texture2D>(folder + file);
            }

            var c = entry.baseColorFactor != null && entry.baseColorFactor.Length >= 4
                ? new Color(entry.baseColorFactor[0], entry.baseColorFactor[1],
                            entry.baseColorFactor[2], entry.baseColorFactor[3])
                : Color.white;
            material.SetColor(isUrp ? "_BaseColor" : "_Color", c);

            var albedo = Tex(entry.baseColor);
            if (albedo != null) material.SetTexture(isUrp ? "_BaseMap" : "_MainTex", albedo);

            var normal = Tex(entry.normal);
            if (normal != null)
            {
                material.SetTexture("_BumpMap", normal);
                material.EnableKeyword("_NORMALMAP");
            }

            var metal = Tex(entry.metallicSmoothness);
            if (metal != null)
            {
                material.SetTexture("_MetallicGlossMap", metal);
                material.EnableKeyword(isUrp ? "_METALLICSPECGLOSSMAP" : "_METALLICGLOSSMAP");
            }
            material.SetFloat("_Metallic", entry.metallic);
            material.SetFloat(isUrp ? "_Smoothness" : "_Glossiness", entry.smoothness);

            var occlusion = Tex(entry.occlusion);
            if (occlusion != null)
            {
                material.SetTexture("_OcclusionMap", occlusion);
                material.EnableKeyword("_OCCLUSIONMAP");
            }

            var emission = Tex(entry.emission);
            if (emission != null)
            {
                material.SetTexture("_EmissionMap", emission);
                material.SetColor("_EmissionColor", Color.white);
                material.EnableKeyword("_EMISSION");
                material.globalIlluminationFlags = MaterialGlobalIlluminationFlags.BakedEmissive;
            }
        }
    }
}
