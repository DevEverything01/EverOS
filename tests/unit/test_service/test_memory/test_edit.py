"""Unit tests for profile editing and episode update."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from everos.memory.models import ExplicitInfoItem, ImplicitTraitItem, Profile
from everos.service.memory._edit import (
    EditOperation,
    EpisodePatch,
    edit_profile,
    update_episode,
)


@pytest.fixture
def mock_stores():
    """Create mock stores."""
    stores = MagicMock()
    stores.profile = AsyncMock()
    stores.episode = AsyncMock()
    return stores


@pytest.fixture
def sample_profile():
    """Create a sample profile."""
    return Profile(
        owner_id="test_user",
        summary="Test summary",
        timestamp=1700000000000,
        explicit_info=[
            ExplicitInfoItem(
                description="Likes coffee",
                category="preferences",
                evidence="mentioned in conversation",
                observed_at=1700000000000,
            )
        ],
        implicit_traits=[
            ImplicitTraitItem(
                description="Detail oriented",
                trait="personality",
                basis="inferred from behavior",
                observed_at=1700000000000,
            )
        ],
    )


class TestEditProfile:
    """Tests for edit_profile function."""

    async def test_add_explicit_info(self, mock_stores):
        """Adding an explicit info item persists to user.md."""
        mock_stores.profile.read.return_value = Profile(
            owner_id="user1",
            summary="",
            timestamp=1700000000000,
            explicit_info=[],
            implicit_traits=[],
        )
        mock_stores.profile.write.return_value = MagicMock()

        result = await edit_profile(
            mock_stores,
            app_id="default",
            project_id="default",
            user_id="user1",
            operations=[
                EditOperation(
                    action="add",
                    item_type="explicit_info",
                    category="preferences",
                    description="Likes blue color",
                )
            ],
        )

        assert result.applied == 1
        assert result.results[0].status == "applied"
        assert result.results[0].item_id.startswith("ei_")
        mock_stores.profile.write.assert_called_once()

    async def test_add_to_nonexistent_profile_creates_one(self, mock_stores):
        """Adding to a user without a profile creates a new profile."""
        mock_stores.profile.read.return_value = None
        mock_stores.profile.write.return_value = MagicMock()

        result = await edit_profile(
            mock_stores,
            app_id="default",
            project_id="default",
            user_id="new_user",
            operations=[
                EditOperation(
                    action="add",
                    item_type="explicit_info",
                    category="General",
                    description="New fact",
                )
            ],
        )

        assert result.applied == 1
        mock_stores.profile.write.assert_called_once()

    async def test_delete_item(self, mock_stores, sample_profile):
        """Deleting an item removes it from the profile."""
        sample_profile.explicit_info[0].model_extra["item_id"] = "ei_12345678"
        mock_stores.profile.read.return_value = sample_profile
        mock_stores.profile.write.return_value = MagicMock()

        result = await edit_profile(
            mock_stores,
            app_id="default",
            project_id="default",
            user_id="test_user",
            operations=[
                EditOperation(
                    action="delete",
                    item_type="explicit_info",
                    item_id="ei_12345678",
                )
            ],
        )

        assert result.applied == 1
        assert result.results[0].status == "applied"
        mock_stores.profile.write.assert_called_once()
        written_profile = mock_stores.profile.write.call_args[0][1]
        assert len(written_profile.explicit_info) == 0

    async def test_delete_nonexistent_item_rejected(self, mock_stores, sample_profile):
        """Deleting a non-existent item is rejected."""
        mock_stores.profile.read.return_value = sample_profile
        mock_stores.profile.write.return_value = MagicMock()

        result = await edit_profile(
            mock_stores,
            app_id="default",
            project_id="default",
            user_id="test_user",
            operations=[
                EditOperation(
                    action="delete",
                    item_type="explicit_info",
                    item_id="ei_nonexistent",
                )
            ],
        )

        assert result.applied == 0
        assert result.results[0].status == "rejected"
        assert result.results[0].message == "item not found"
        mock_stores.profile.write.assert_not_called()

    async def test_update_item(self, mock_stores, sample_profile):
        """Updating an item modifies the description."""
        sample_profile.explicit_info[0].model_extra["item_id"] = "ei_12345678"
        mock_stores.profile.read.return_value = sample_profile
        mock_stores.profile.write.return_value = MagicMock()

        result = await edit_profile(
            mock_stores,
            app_id="default",
            project_id="default",
            user_id="test_user",
            operations=[
                EditOperation(
                    action="update",
                    item_type="explicit_info",
                    item_id="ei_12345678",
                    description="Updated description",
                )
            ],
        )

        assert result.applied == 1
        assert result.results[0].status == "applied"
        mock_stores.profile.write.assert_called_once()
        written_profile = mock_stores.profile.write.call_args[0][1]
        assert written_profile.explicit_info[0].description == "Updated description"


class TestUpdateEpisode:
    """Tests for update_episode function."""

    async def test_update_episode_summary(self, mock_stores):
        """Updating an episode modifies the daily file."""
        from everos.memory.models import Episode

        mock_episode = Episode(
            owner_id="user1",
            episode="Original content",
            summary="Original summary",
            subject="Original subject",
            timestamp=1700000000000,
            parent_id="mc_1234",
        )

        mock_stored = MagicMock()
        mock_stored.item = mock_episode
        mock_stored.ref = MagicMock(entry_id="ep_20231115_001")

        mock_stores.episode.read_many.return_value = [mock_stored]
        mock_stores.episode.replace.return_value = True

        result = await update_episode(
            mock_stores,
            app_id="default",
            project_id="default",
            memory_id="user1_ep_20231115_001",
            patch=EpisodePatch(summary="New summary"),
        )

        assert not result.unchanged
        assert result.id == "user1_ep_20231115_001"
        assert result.edited_at is not None
        mock_stores.episode.replace.assert_awaited_once()
        mock_stores.episode.remove.assert_not_called()
        mock_stores.episode.append.assert_not_called()

    async def test_update_episode_no_change(self, mock_stores):
        """Sending the same values returns unchanged=True."""
        from everos.memory.models import Episode

        mock_episode = Episode(
            owner_id="user1",
            episode="Content",
            summary="Summary",
            subject="Subject",
            timestamp=1700000000000,
            parent_id="mc_1234",
        )

        mock_stored = MagicMock()
        mock_stored.item = mock_episode
        mock_stored.ref = MagicMock(entry_id="ep_20231115_001")

        mock_stores.episode.read_many.return_value = [mock_stored]

        result = await update_episode(
            mock_stores,
            app_id="default",
            project_id="default",
            memory_id="user1_ep_20231115_001",
            patch=EpisodePatch(summary="Summary"),  # Same value
        )

        assert result.unchanged
        mock_stores.episode.remove.assert_not_called()

    async def test_update_nonexistent_episode(self, mock_stores):
        """Updating a non-existent episode returns unchanged=True."""
        mock_stores.episode.read_many.return_value = []

        result = await update_episode(
            mock_stores,
            app_id="default",
            project_id="default",
            memory_id="user1_ep_20231115_001",
            patch=EpisodePatch(summary="New summary"),
        )

        assert result.unchanged
