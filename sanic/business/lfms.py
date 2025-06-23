import services.redis as redis_client
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import LfmRequestApiModel, LfmRequestType
from models.lfm import Lfm, LfmActivity, LfmActivityEvent, LfmActivityType
from models.redis import ServerLfmData

from utils.time import get_current_datetime_string


def handle_incoming_lfms(request_body: LfmRequestApiModel, type: LfmRequestType):
    # useful stuff
    deleted_ids = set(request_body.deleted_ids)

    # set up the main dicts
    lfms_by_server_name: dict[str, ServerLfmData] = {
        server_name: ServerLfmData(lfms={}) for server_name in SERVER_NAMES_LOWERCASE
    }

    # organize the lfms into their servers
    for lfm in request_body.lfms:
        server_name_lower = lfm.server_name.lower()
        if not server_name_lower in SERVER_NAMES_LOWERCASE:
            continue

        lfm.last_update = get_current_datetime_string()
        lfms_by_server_name[server_name_lower].lfms[lfm.id] = lfm

    # go through each server...
    for server_name, server_lfm_data in lfms_by_server_name.items():
        incoming_lfms: dict[int, dict] = {
            lfm_id: lfm.model_dump() for lfm_id, lfm in server_lfm_data.lfms.items()
        }
        previous_lfms_data = redis_client.get_lfms_by_server_name(server_name)

        lfm_activity = get_lfm_activity(previous_lfms_data, server_lfm_data.lfms)
        hydrated_lfms = hydrate_lfms_with_activity(incoming_lfms, lfm_activity)

        if type == LfmRequestType.set:
            redis_client.set_lfms_by_server_name(hydrated_lfms, server_name)
        elif type == LfmRequestType.update:
            redis_client.update_lfms_by_server_name(hydrated_lfms, server_name)
            redis_client.delete_lfms_by_id_and_server_name(deleted_ids, server_name)


def get_lfm_activity(
    previous_lfms: dict[int, Lfm], current_lfms: dict[int, Lfm]
) -> dict[int, list[dict]]:
    """Returns a dict of lfm id to list of activity"""
    previous_lfm_ids = set(previous_lfms.keys())
    lfm_activity: dict[int, list[dict]] = {}
    for lfm_id, current_lfm in current_lfms.items():
        try:
            previous_lfm = previous_lfms[lfm_id] if lfm_id in previous_lfms else None

            old_lfm_activity: list[LfmActivity] = []
            new_activity_events_list: list[LfmActivityEvent] = []

            is_lfm_new = False

            # lfms that were just posted:
            if lfm_id not in previous_lfm_ids:
                new_activity_events_list.append(
                    LfmActivityEvent(tag=LfmActivityType.posted)
                )
                is_lfm_new = True  # no need to check for other updates

            if not is_lfm_new:
                # carry over activity from previous lfms data
                if previous_lfms[lfm_id].activity:
                    old_lfm_activity = previous_lfms[lfm_id].activity

                # quest updated:
                if previous_lfm.quest_id != current_lfm.quest_id:
                    new_activity_events_list.append(
                        LfmActivityEvent(
                            tag=LfmActivityType.quest,
                            data=str(current_lfm.quest_id or 0),
                        )
                    )

                # comment updated:
                if previous_lfm.comment != current_lfm.comment:
                    new_activity_events_list.append(
                        LfmActivityEvent(
                            tag=LfmActivityType.comment, data=current_lfm.comment
                        )
                    )

                # members left or joined:
                old_member_ids = {member.id for member in previous_lfm.members}
                new_member_ids = {member.id for member in current_lfm.members}
                members_left = old_member_ids - new_member_ids
                members_joined = new_member_ids - old_member_ids
                # TODO: nested loops, should be optimized
                for member_id in members_left:
                    # get the name of the member that left
                    member_name = "Unknown"
                    if previous_lfm:
                        for member in previous_lfm.members:
                            if member.id == member_id:
                                member_name = member.name
                                break
                    new_activity_events_list.append(
                        LfmActivityEvent(
                            tag=LfmActivityType.member_left,
                            data=member_name,
                        )
                    )
                # TODO: nested loops, should be optimized
                for member_id in members_joined:
                    # get the name of the member that left
                    member_name = "Unknown"
                    for member in current_lfm.members:
                        if member.id == member_id:
                            member_name = member.name
                            break
                    new_activity_events_list.append(
                        LfmActivityEvent(
                            tag=LfmActivityType.member_joined,
                            data=member_name,
                        )
                    )

            # comine the old and new activity
            new_lfm_activity = LfmActivity(
                timestamp=current_lfm.last_update,
                events=new_activity_events_list,
            )
            aggregate_activity = old_lfm_activity + (
                [new_lfm_activity] if new_activity_events_list else []
            )
            lfm_activity[lfm_id] = [
                activity.model_dump() for activity in aggregate_activity
            ]
        except Exception as e:
            print(f"Error processing LFM ID {lfm_id} (skipping): {e}")
            pass

    return lfm_activity


def hydrate_lfms_with_activity(
    lfms: dict[int, dict], lfm_activity: dict[int, list[dict]]
) -> dict[int, dict]:
    """Hydrates lfms with activity"""
    lfms_with_activity: dict[int, dict] = {}
    for lfm_id, lfm in lfms.items():
        lfm_with_activity = {**lfm, "activity": lfm_activity[lfm_id]}
        lfms_with_activity[lfm_id] = lfm_with_activity
    return lfms_with_activity
