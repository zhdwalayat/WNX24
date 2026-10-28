"""broadcast_os/scenes — one renderer per SceneType, each turning a
ScenePackage into a list[np.ndarray] of 1920x1080 RGB frames."""
from broadcast_os.scenes import (
    analysis_scene,
    data_scene,
    footage_scene,
    headline_scene,
    intro_scene,
    map_scene,
    outro_scene,
    summary_scene,
)

SCENE_RENDERERS = {
    "intro": intro_scene.render,
    "headline": headline_scene.render,
    "footage": footage_scene.render,
    "map": map_scene.render,
    "data": data_scene.render,
    "analysis": analysis_scene.render,
    "summary": summary_scene.render,
    "outro": outro_scene.render,
}
