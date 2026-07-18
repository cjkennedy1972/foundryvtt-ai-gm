"""Tests for settlement generation and querying."""

import unittest
from unittest.mock import MagicMock
import sys
import os

# Add the project root to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'ai-engine'))

from procedural.settlement import (    Building, BuildingType, OccupationType,
    Religion, ScheduleEntry, Settlement, SettlementNPC,
    SettlementSize, ServiceType, TimeSlot, TypedRelationship,
    NPCSchedule,
)
from procedural.settlement_gen import SettlementGenerator
from procedural.generator import ProceduralGenerator

class TestTimeSlot(unittest.TestCase):
    def test_for_hour(self):
        self.assertEqual(TimeSlot.for_hour(7), TimeSlot.MORNING)
        self.assertEqual(TimeSlot.for_hour(10), TimeSlot.MORNING)
        self.assertEqual(TimeSlot.for_hour(13), TimeSlot.AFTERNOON)
        self.assertEqual(TimeSlot.for_hour(17), TimeSlot.AFTERNOON)
        self.assertEqual(TimeSlot.for_hour(19), TimeSlot.DUSK)
        self.assertEqual(TimeSlot.for_hour(22), TimeSlot.EVENING)
        self.assertEqual(TimeSlot.for_hour(0), TimeSlot.EVENING)
        self.assertEqual(TimeSlot.for_hour(2), TimeSlot.NIGHT)

    def test_all_slots(self):
        slots = TimeSlot.all_slots()
        self.assertEqual(len(slots), 5)

class TestBuilding(unittest.TestCase):
    def test_building_creation(self):
        b = Building(name="Test", building_type="tavern", services=["food_and_drink"])
        self.assertEqual(b.name, "Test")
        self.assertIn("food_and_drink", b.services)

    def test_has_service(self):
        b = Building(name="Test", building_type="shop", services=["surplus"])
        self.assertTrue(b.has_service("surplus"))
        self.assertFalse(b.has_service("brewing"))

    def test_to_dict(self):
        b = Building(name="Test", building_type="tavern")
        d = b.to_dict()
        self.assertEqual(d["name"], "Test")
        self.assertEqual(d["type"], "tavern")

class TestSettlement(unittest.TestCase):
    def test_add_building(self):
        s = Settlement(name="Test", size="hamlet")
        b = Building(name="B1", building_type="tavern")
        s.add_building(b)
        self.assertEqual(len(s.buildings), 1)

    def test_add_npc(self):
        s = Settlement(name="Test", size="hamlet")
        npc = SettlementNPC(name="Alice", occupation="blacksmith")
        s.add_npc(npc)
        self.assertEqual(len(s.npcs), 1)

    def test_query_at_time(self):
        s = Settlement(name="Test", size="hamlet")
        tavern = Building(name="Tavern", building_type="tavern", services=["food_and_drink"])
        s.add_building(tavern)

        npc1 = SettlementNPC(name="Alice", occupation="tavernkeeper", building="Tavern")
        sched = NPCSchedule(npc_name="Alice")
        sched.add_entry("morning", "Tavern", "Opening")
        sched.add_entry("afternoon", "Tavern", "Serving")
        npc1.schedule = sched

        npc2 = SettlementNPC(name="Bob", occupation="farmer", building="Farm")
        sched2 = NPCSchedule(npc_name="Bob")
        sched2.add_entry("morning", "Farm", "Working")
        npc2.schedule = sched2

        s.add_npc(npc1)
        s.add_npc(npc2)

        tavern_at_morning = s.query_at_time("morning", location="Tavern")
        self.assertEqual(len(tavern_at_morning), 1)
        self.assertEqual(tavern_at_morning[0].name, "Alice")

        anyone_at_dusk = s.query_at_time("dusk")
        self.assertEqual(len(anyone_at_dusk), 2)

    def test_query_building(self):
        s = Settlement(name="Test", size="hamlet")
        b = Building(name="B1", building_type="tavern")
        s.add_building(b)
        self.assertIsNotNone(s.query_building("B1"))
        self.assertIsNone(s.query_building("Nonexistent"))

    def test_query_building_by_service(self):
        s = Settlement(name="Test", size="hamlet")
        tavern = Building(name="Tavern", building_type="tavern", services=["food_and_drink"])
        shop = Building(name="Shop", building_type="shop", services=["surplus"])
        s.add_building(tavern)
        s.add_building(shop)
        food = s.query_building_by_service("food_and_drink")
        self.assertEqual(len(food), 1)
        self.assertEqual(food[0].name, "Tavern")

    def test_time_summary(self):
        s = Settlement(name="Test", size="hamlet")
        tavern = Building(name="Tavern", building_type="tavern")
        s.add_building(tavern)
        npc1 = SettlementNPC(name="Alice", occupation="tavernkeeper", building="Tavern")
        sched = NPCSchedule(npc_name="Alice")
        sched.add_entry("morning", "Tavern", "Opening")
        npc1.schedule = sched
        s.add_npc(npc1)
        summary = s.time_summary("morning")
        self.assertIn("Tavern", summary)
        self.assertIn("Alice", summary["Tavern"])

class TestSettlementGenerator(unittest.TestCase):
    def _make_mock_gen(self):
        gen = SettlementGenerator(rng=MagicMock())
        # Use indexed counters so choice() returns different elements each time
        _counter = {'i': 0}
        def _choice(lst):
            if not lst:
                return None
            idx = _counter['i'] % len(lst)
            _counter['i'] += 1
            return lst[idx]
        gen.rng.randint = lambda a, b: 100
        gen.rng.choice = _choice
        gen.rng.sample = lambda lst, n: lst[:n] if n <= len(lst) else lst
        gen.rng.uniform = lambda a, b: 0.5
        return gen

    def test_generate_hamlet(self):
        gen = self._make_mock_gen()
        s = gen.generate("Hamlet", "hamlet", num_npcs=10, num_buildings=5)
        self.assertEqual(s.name, "Hamlet")
        self.assertEqual(s.size, "hamlet")
        self.assertEqual(len(s.buildings), 5)
        self.assertEqual(len(s.npcs), 10)

    def test_generate_village(self):
        gen = self._make_mock_gen()
        s = gen.generate("Village", "village", num_npcs=50, num_buildings=20)
        self.assertEqual(s.name, "Village")
        self.assertEqual(s.size, "village")
        self.assertEqual(len(s.buildings), 20)
        self.assertEqual(len(s.npcs), 50)

    def test_serialization(self):
        gen = self._make_mock_gen()
        s = gen.generate("Test", "hamlet", num_npcs=5, num_buildings=5)
        d = s.to_dict()
        self.assertEqual(d["name"], "Test")
        self.assertIn("buildings", d)
        self.assertIn("npcs", d)
        self.assertIn("religions", d)

    def test_npc_have_schedules(self):
        gen = self._make_mock_gen()
        s = gen.generate("Test", "hamlet", num_npcs=10, num_buildings=5)
        for npc in s.npcs:
            self.assertIsNotNone(npc.schedule)
            self.assertGreater(len(npc.schedule.entries), 0)

    def test_npc_have_relationships(self):
        gen = self._make_mock_gen()
        s = gen.generate("Test", "hamlet", num_npcs=20, num_buildings=10)
        npcs_with_rels = [n for n in s.npcs if n.relationships]
        self.assertGreater(len(npcs_with_rels), 0)

    def test_settlement_has_religions(self):
        gen = self._make_mock_gen()
        s = gen.generate("Test", "hamlet", num_npcs=10, num_buildings=5)
        self.assertGreater(len(s.religions), 0)

class TestProceduralGeneratorIntegration(unittest.TestCase):
    def test_generate_settlement(self):
        pg = ProceduralGenerator()
        result = pg.roll_all("settlement", name="Test Town", size="village")
        # roll_all returns the Settlement object directly for settlements
        self.assertEqual(result.name, "Test Town")
        self.assertEqual(result.size, "village")
        self.assertGreater(len(result.buildings), 0)
        self.assertGreater(len(result.npcs), 0)

if __name__ == '__main__':
    unittest.main()
