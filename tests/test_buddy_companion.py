"""Tests for buddy companion generation (Phase 1)."""
from buddy.companion import (
    hash_string,
    mulberry32,
    pick,
    roll,
    roll_rarity,
    roll_stats,
    roll_with_seed,
)
from buddy.types import (
    RARITIES,
    RARITY_FLOOR,
    RARITY_WEIGHTS,
    SPECIES,
    STAT_NAMES,
)


class TestMulberry32:
    def test_deterministic(self):
        """Same seed always produces the same sequence."""
        rng1 = mulberry32(12345)
        rng2 = mulberry32(12345)
        for _ in range(100):
            assert rng1() == rng2()

    def test_range(self):
        """All values in [0, 1)."""
        rng = mulberry32(42)
        for _ in range(1000):
            v = rng()
            assert 0.0 <= v < 1.0

    def test_different_seeds_differ(self):
        """Different seeds produce different sequences."""
        rng1 = mulberry32(1)
        rng2 = mulberry32(2)
        # At least one of the first 10 values should differ
        assert any(rng1() != rng2() for _ in range(10))


class TestHashString:
    def test_deterministic(self):
        assert hash_string("hello") == hash_string("hello")

    def test_different_strings_differ(self):
        assert hash_string("hello") != hash_string("world")

    def test_32bit(self):
        """Result fits in 32 bits."""
        h = hash_string("test string with unicode: 你好")
        assert 0 <= h <= 0xFFFFFFFF


class TestRollRarity:
    def test_weights_sum(self):
        """Weights sum to 100."""
        assert sum(RARITY_WEIGHTS.values()) == 100

    def test_all_rarities_possible(self):
        """Over many rolls, all rarities should appear."""
        seen = set()
        rng = mulberry32(0)
        for _ in range(10000):
            seen.add(roll_rarity(rng))
        assert seen == set(RARITIES)

    def test_common_most_frequent(self):
        """Common should be the most frequent rarity."""
        counts: dict[str, int] = {r: 0 for r in RARITIES}
        rng = mulberry32(999)
        for _ in range(10000):
            counts[roll_rarity(rng)] += 1
        assert counts['common'] > counts['uncommon'] > counts['rare']


class TestRollStats:
    def test_stat_names(self):
        """All 5 stats are present."""
        rng = mulberry32(42)
        stats = roll_stats(rng, 'common')
        assert set(stats.keys()) == set(STAT_NAMES)

    def test_stat_range(self):
        """Stats are in [1, 100]."""
        for seed in range(100):
            rng = mulberry32(seed)
            for rarity in RARITIES:
                stats = roll_stats(rng, rarity)
                for name, val in stats.items():
                    assert 1 <= val <= 100, f"{name}={val} for rarity={rarity}, seed={seed}"

    def test_rarity_floor_respected(self):
        """Higher rarity → generally higher stats (at least peak stat)."""
        rng = mulberry32(42)
        common_stats = roll_stats(rng, 'common')
        rng = mulberry32(42)
        legendary_stats = roll_stats(rng, 'legendary')
        # The legendary peak stat should be higher than common floor
        assert max(legendary_stats.values()) >= RARITY_FLOOR['legendary'] + 50


class TestRoll:
    def test_deterministic(self):
        """Same user_id always gives same companion."""
        r1 = roll("test-user-123")
        r2 = roll("test-user-123")
        assert r1.bones.species == r2.bones.species
        assert r1.bones.rarity == r2.bones.rarity
        assert r1.bones.eye == r2.bones.eye
        assert r1.bones.stats == r2.bones.stats

    def test_different_users_differ(self):
        """Different users get different companions (with high probability)."""
        r1 = roll_with_seed("user-alice")
        r2 = roll_with_seed("user-bob")
        # At least one attribute should differ
        assert (r1.bones.species != r2.bones.species or
                r1.bones.rarity != r2.bones.rarity or
                r1.bones.eye != r2.bones.eye)

    def test_valid_species(self):
        r = roll_with_seed("test")
        assert r.bones.species in SPECIES

    def test_valid_rarity(self):
        r = roll_with_seed("test")
        assert r.bones.rarity in RARITIES

    def test_common_gets_no_hat(self):
        """Common rarity companions always get hat='none'."""
        # Roll many seeds until we find a common one
        for i in range(1000):
            r = roll_with_seed(f"seed-{i}")
            if r.bones.rarity == 'common':
                assert r.bones.hat == 'none'
                return
        # If we didn't find a common in 1000 tries, that's statistically impossible
        assert False, "No common rarity found in 1000 rolls"
