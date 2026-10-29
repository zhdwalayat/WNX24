"""broadcast_os/graphics — motion-graphics generators for the BroadcastOS
scene renderers. Each generator exposes a `frame(t: float, **kwargs) ->
PIL.Image.Image` function (or class method) that returns one fully composed
1920x1080 RGB frame for animation progress `t` in [0, 1]."""
