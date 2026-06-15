"""Ambient environment management - weather, time of day, atmosphere."""

import logging
from typing import Optional, Dict, List
from enum import Enum

logger = logging.getLogger(__name__)


class WeatherType(Enum):
    """Weather conditions in D&D 5e."""
    CLEAR = "clear"
    CLOUDY = "cloudy"
    RAIN = "rain"
    THUNDERSTORM = "thunderstorm"
    SNOW = "snow"
    BLIZZARD = "blizzard"
    FOG = "fog"
    MIST = "mist"
    HEAT_WAVE = "heat_wave"
    TORNADO = "tornado"


class TimeOfDay(Enum):
    """Time of day for atmosphere."""
    DAWN = "dawn"           # 06:00-06:59
    MORNING = "morning"     # 07:00-11:59
    NOON = "noon"           # 12:00-12:59
    AFTERNOON = "afternoon" # 13:00-16:59
    DUSK = "dusk"           # 17:00-17:59
    EVENING = "evening"     # 18:00-20:59
    NIGHT = "night"         # 21:00-05:59


class AmbientManager:
    """Manage ambient environment and atmosphere for immersion."""

    WEATHER_EFFECTS = {
        WeatherType.CLEAR: {
            "description": "Clear skies with excellent visibility",
            "visibility_bonus": 0,
            "temperature": "moderate",
        },
        WeatherType.RAIN: {
            "description": "Rain falling steadily, visibility reduced",
            "visibility_bonus": -1,
            "temperature": "cool",
            "sound": "rain_medium",
        },
        WeatherType.THUNDERSTORM: {
            "description": "Violent thunderstorm with heavy rain and lightning",
            "visibility_bonus": -2,
            "temperature": "cold",
            "sound": "thunderstorm",
            "light_effects": "lightning_flashes",
        },
        WeatherType.SNOW: {
            "description": "Snow falling lightly, ground covered",
            "visibility_bonus": -1,
            "temperature": "freezing",
            "sound": "snow_ambient",
        },
        WeatherType.FOG: {
            "description": "Thick fog limiting visibility significantly",
            "visibility_bonus": -3,
            "temperature": "damp",
        },
        WeatherType.HEAT_WAVE: {
            "description": "Intense heat, shimmering air, exhaustion risk",
            "visibility_bonus": -1,
            "temperature": "scorching",
            "status_effect": "exhaustion",
        },
    }

    TIME_ATMOSPHERE = {
        TimeOfDay.DAWN: {
            "description": "First light breaking over the horizon",
            "lighting": "dim",
            "color_tone": "orange_red",
            "visibility": "poor",
        },
        TimeOfDay.MORNING: {
            "description": "Bright morning light, clear visibility",
            "lighting": "bright",
            "color_tone": "golden",
            "visibility": "excellent",
        },
        TimeOfDay.NOON: {
            "description": "Full daylight, harsh shadows, high heat",
            "lighting": "bright",
            "color_tone": "white",
            "visibility": "excellent",
            "heat_effect": True,
        },
        TimeOfDay.AFTERNOON: {
            "description": "Late day light, long shadows",
            "lighting": "bright",
            "color_tone": "golden",
            "visibility": "excellent",
        },
        TimeOfDay.DUSK: {
            "description": "Sun setting, twilight colors filling the sky",
            "lighting": "dim",
            "color_tone": "purple_orange",
            "visibility": "poor",
        },
        TimeOfDay.EVENING: {
            "description": "Darkness falling, stars appearing",
            "lighting": "dark",
            "color_tone": "blue_purple",
            "visibility": "very_poor",
            "need_light": True,
        },
        TimeOfDay.NIGHT: {
            "description": "Full darkness, only moonlight or artificial light",
            "lighting": "dark",
            "color_tone": "blue_gray",
            "visibility": "very_poor",
            "need_light": True,
        },
    }

    def __init__(self):
        self.current_weather = WeatherType.CLEAR
        self.current_time = TimeOfDay.NOON
        self.atmospheric_effects: List[str] = []

    def set_weather(self, weather: WeatherType) -> Dict:
        """Set current weather condition."""
        self.current_weather = weather
        effect = self.WEATHER_EFFECTS.get(weather, {})

        logger.info(f"[Weather] Changed to {weather.value}: {effect.get('description', '')}")

        return {
            "type": "weather_changed",
            "weather": weather.value,
            "description": effect.get("description", ""),
            "effects": {
                "visibility_modifier": effect.get("visibility_bonus", 0),
                "temperature": effect.get("temperature", ""),
                "sound": effect.get("sound"),
                "lighting": effect.get("light_effects"),
                "status_effect": effect.get("status_effect"),
            },
        }

    def set_time(self, time_of_day: TimeOfDay) -> Dict:
        """Set time of day for atmosphere."""
        self.current_time = time_of_day
        atmosphere = self.TIME_ATMOSPHERE.get(time_of_day, {})

        logger.info(f"[Time] Set to {time_of_day.value}: {atmosphere.get('description', '')}")

        return {
            "type": "time_changed",
            "time": time_of_day.value,
            "description": atmosphere.get("description", ""),
            "effects": {
                "lighting": atmosphere.get("lighting", ""),
                "color_tone": atmosphere.get("color_tone", ""),
                "visibility": atmosphere.get("visibility", ""),
                "need_light_sources": atmosphere.get("need_light", False),
                "heat_effect": atmosphere.get("heat_effect", False),
            },
        }

    def add_atmospheric_effect(self, effect: str) -> Dict:
        """Add a temporary atmospheric effect."""
        self.atmospheric_effects.append(effect)
        logger.info(f"[Atmosphere] Added effect: {effect}")

        return {
            "type": "atmospheric_effect_added",
            "effect": effect,
            "active_effects": self.atmospheric_effects,
        }

    def get_atmosphere_description(self) -> str:
        """Get full atmospheric description for narrative."""
        weather_desc = self.WEATHER_EFFECTS.get(
            self.current_weather, {}
        ).get("description", "")
        time_desc = self.TIME_ATMOSPHERE.get(
            self.current_time, {}
        ).get("description", "")

        effects_text = (
            f" You notice {', '.join(self.atmospheric_effects)}."
            if self.atmospheric_effects
            else ""
        )

        return f"{time_desc} {weather_desc}{effects_text}"

    def get_environmental_modifiers(self) -> Dict:
        """Get all current environmental modifiers for combat."""
        weather_effect = self.WEATHER_EFFECTS.get(self.current_weather, {})
        time_effect = self.TIME_ATMOSPHERE.get(self.current_time, {})

        return {
            "weather": self.current_weather.value,
            "time": self.current_time.value,
            "visibility_modifier": weather_effect.get("visibility_bonus", 0),
            "light_condition": time_effect.get("lighting", "bright"),
            "darkness_enabled": time_effect.get("need_light", False),
            "active_effects": self.atmospheric_effects,
        }
