"""stand_trans: Parametric Building JSON -> BIM JSON -> IFC -> GLB."""

__all__ = ["Result", "convert"]
__version__ = "0.1.0"


# Lazy: importing ``stand_trans.blast`` / ``stand_trans.materials`` should NOT pull
# in the full modeling pipeline (ifcopenshell / trimesh / manifold3d). The blast
# physics only needs numpy (+ optional materials table), so model-registry's main
# (light) image can import ``stand_trans.blast`` for the in-process /blast/quick
# endpoint without the heavy CAD deps. ``convert`` / ``Result`` stay available via
# attribute access for the docker-sandbox path (heavy image has all deps).
def __getattr__(name):  # PEP 562
    if name in ("Result", "convert"):
        from .pipeline import Result, convert

        return {"Result": Result, "convert": convert}[name]
    raise AttributeError(f"module 'stand_trans' has no attribute {name!r}")
