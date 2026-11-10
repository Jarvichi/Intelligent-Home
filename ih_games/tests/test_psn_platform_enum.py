"""psn_trophy_names passes psnawp the PlatformType enum, not text (0.9.8). Fake psnawp, no network."""
import enum, os, sys, types, unittest
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))


class PlatformType(enum.Enum):
    PS4 = "PS4"
    PS5 = "PS5"


SEEN = {}


class Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeUser:
    def trophy_titles(self, limit=None):
        return [Obj(title_name="Cyberpunk 2077", np_communication_id="NPWR1", title_platform=frozenset({PlatformType.PS5}))]

    def trophy_groups_summary(self, comm_id, platform):
        SEEN["groups"] = platform
        return [Obj(trophy_group_id="default")]

    def trophies(self, comm_id, platform, include_progress=True, trophy_group_id="default", limit=None):
        SEEN["trophies"] = platform
        return [Obj(earned=True, trophy_name="The Fool", earned_date_time="2026-11-10T20:00:00", trophy_type="TrophyType.BRONZE", trophy_id=1)]


class FakeClient:
    def __init__(self, npsso_cookie=None):
        pass

    def me(self):
        return Obj(online_id="Jarvichi", trophy_titles=FakeUser().trophy_titles, trophy_groups_summary=FakeUser().trophy_groups_summary, trophies=FakeUser().trophies)


def install_fake():
    for name in ("psnawp_api", "psnawp_api.models", "psnawp_api.models.trophies", "psnawp_api.models.trophies.trophy_constants"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["psnawp_api"].PSNAWP = FakeClient
    sys.modules["psnawp_api.models.trophies.trophy_constants"].PlatformType = PlatformType


class PlatformEnumTest(unittest.TestCase):
    def test_platform_is_enum_member(self):
        saved = {k: sys.modules.get(k) for k in list(sys.modules) if k.startswith("psnawp_api")}
        install_fake()
        try:
            from providers import psn_trophy_names as p
            out = p._recent_trophies("tok", "Jarvichi", "Cyberpunk 2077")
        finally:
            for k in [k for k in sys.modules if k.startswith("psnawp_api")]:
                del sys.modules[k]
            sys.modules.update({k: v for k, v in saved.items() if v})
        self.assertIs(SEEN["groups"], PlatformType.PS5)
        self.assertIs(SEEN["trophies"], PlatformType.PS5)
        self.assertEqual(out[0]["name"], "The Fool")


if __name__ == "__main__":
    unittest.main()
