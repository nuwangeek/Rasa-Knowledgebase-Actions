from dis import dis
from rasa_sdk.executor import CollectingDispatcher
import asyncio
#
#
import logging
import os
import re
from typing import Text, Dict, Any, List, cast, Callable
from rasa_sdk import Tracker, utils
from rasa_sdk.events import SlotSet
from rasa_sdk.knowledge_base.utils import SLOT_OBJECT_TYPE, SLOT_ATTRIBUTE, \
    SLOT_LAST_OBJECT_TYPE, reset_attribute_slots, \
    SLOT_MENTION, SLOT_LAST_OBJECT, SLOT_LISTED_OBJECTS, get_object_name, get_attribute_slots
from rasa_sdk.types import DomainDict
from rasa_sdk.knowledge_base.storage import InMemoryKnowledgeBase
from rasa_sdk.knowledge_base.actions import ActionQueryKnowledgeBase
logger = logging.getLogger(__name__)


class ActionMyKB(ActionQueryKnowledgeBase):
    def __init__(self):
        # load knowledge base with data from the given file
        self.knowledge_base = InMemoryKnowledgeBase("knowledge_base_data.json")
        # overwrite the representation function of the hotel object
        # by default the representation function is just the name of the object
        # knowledge_base.set_representation_function_of_object(
        #     "hotel", lambda obj: obj["name"] + " (" + obj["city"] + ")"
        # )
        
        super().__init__(self.knowledge_base)

    def name(self) -> Text:

         return "action_query_knowledge_base"

    async def run(
                    self,
                    dispatcher: CollectingDispatcher,
                    tracker: Tracker,
                    domain: Dict[Text, Any],
                ) -> List[Dict[Text, Any]]:
                    """
                    Executes this action. If the user ask a question about an attribute,
                    the knowledge base is queried for that attribute. Otherwise, if no
                    attribute was detected in the request or the user is talking about a new
                    object type, multiple objects of the requested type are returned from the
                    knowledge base.
                    Args:
                        dispatcher: the dispatcher
                        tracker: the tracker
                        domain: the domain
                    Returns: list of slots
                    """
                    object_type = tracker.get_slot(SLOT_OBJECT_TYPE)
                    last_object_type = tracker.get_slot(SLOT_LAST_OBJECT_TYPE)
                    attribute = tracker.get_slot(SLOT_ATTRIBUTE)
                    has_mention = tracker.get_slot(SLOT_MENTION) is not None

                    new_request = object_type != last_object_type

                    if not object_type:
                        # object type always needs to be set as this is needed to query the
                        # knowledge base
                        dispatcher.utter_message(template="utter_ask_rephrase")
                        return []

                    if last_object_type and new_request and has_mention:
                        return await self._query_join_objects(dispatcher, object_type, last_object_type, tracker)
                    elif not attribute:
                        return await self._query_objects(dispatcher, object_type, tracker)
                    elif attribute:
                        return await self._query_attribute(
                            dispatcher, object_type, attribute, tracker
                        )

                    dispatcher.utter_message(template="utter_ask_rephrase")

                    return []

    async def _query_objects(
            self, dispatcher: CollectingDispatcher, object_type: Text,
            tracker: Tracker
    ) -> List[Dict]:
        """
        Copied from ActionQueryKnowledgeBase and overridden
        in order to introduce LIMIT parameter
        Queries the knowledge base for objects of the requested object type and
        outputs those to the user. The objects are filtered by any attribute the
        user mentioned in the request.
        Args:
            dispatcher: the dispatcher
            tracker: the tracker
        Returns: list of slots
        """
        object_attributes = await utils.call_potential_coroutine(
            self.knowledge_base.get_attributes_of_object(object_type)
        )
        # print("------------------------------")
        # print("Enter _query_objects")
        # print("------------------------------")
        # get all set attribute slots of the object type to be able to filter the
        # list of objects
        # limit = tracker.get_slot(SLOT_LIMIT)
        # logger.info(f"Limit is {limit}")
        attributes = get_attribute_slots(tracker, object_attributes)
        # var_args = {}
        # if limit:
        #     var_args['limit'] = int(limit)

        # query the knowledge base
        objects = await utils.call_potential_coroutine(
            self.knowledge_base.get_objects(object_type, attributes)
        )

        await utils.call_potential_coroutine(
            self.utter_objects(dispatcher, object_type, objects, attributes)
        )

        if not objects:
            return reset_attribute_slots(tracker, object_attributes)

        key_attribute = await utils.call_potential_coroutine(
            self.knowledge_base.get_key_attribute_of_object(object_type)
        )

        last_object = None if len(objects) > 1 else objects[0][key_attribute]

        slots = [
            SlotSet(SLOT_OBJECT_TYPE, object_type),
            SlotSet(SLOT_MENTION, None),
            SlotSet(SLOT_ATTRIBUTE, None),
            SlotSet(SLOT_LAST_OBJECT, last_object),
            SlotSet(SLOT_LAST_OBJECT_TYPE, object_type),
            SlotSet(
                SLOT_LISTED_OBJECTS,
                list(map(lambda e: e[key_attribute], objects))
            ),
            
        ]

        return slots + reset_attribute_slots(tracker, object_attributes)

    async def utter_objects(
            self,
            dispatcher: CollectingDispatcher,
            object_type: Text,
            objects: List[Dict[Text, Any]],
            attributes: List[Dict[Text, Text]] = None,
    ):
        """
        Utters a response to the user that lists all found objects.
        Args:
            dispatcher: the dispatcher
            object_type: the object type
            objects: the list of objects
        """
        attributes_repr = f" with {', '.join([attribute['name'] + ': ' + attribute['value'] for attribute in attributes])}" if attributes else ""
        if objects:
            dispatcher.utter_message(
                text=f"I found the following {object_type}s {attributes_repr}:"
            )

            repr_function = await utils.call_potential_coroutine(
                self.knowledge_base.get_representation_function_of_object(object_type)
            )

            for i, obj in enumerate(objects, 1):
                dispatcher.utter_message(text=f"{i}: {repr_function(obj)}")
        else:
            dispatcher.utter_message(
                text=f"I could not find any {object_type}s {attributes_repr}."
            )

    def utter_attribute_value(
            self,
            dispatcher: CollectingDispatcher,
            object_name: Text,
            attribute_name: Text,
            attribute_value: Text,
    ):
        """
        Utters a response that informs the user about the attribute value of the
        attribute of interest.
        Args:
            dispatcher: the dispatcher
            object_name: the name of the object
            attribute_name: the name of the attribute
            attribute_value: the value of the attribute
        """
        if attribute_value:
            dispatcher.utter_message(
                text=f"The {attribute_name} of \"{object_name}\" is \n\n \"{str(attribute_value)}\"."
            )
        else:
            dispatcher.utter_message(
                text=f"Did not find a valid value for attribute '{attribute_name}' for object '{object_name}''{attribute_name}'."
            )

    async def _query_join_objects(self, dispatcher: CollectingDispatcher, object_type: Text, last_object_type, tracker: Tracker) -> List[Dict]:
        """
        Performs a join query on the knowledge base
        by returning the object of type 'object_type'
        where the key attribute matches the key of the last object in the tracker.
        Args:
            dispatcher: the dispatcher
            tracker: the tracker
        Returns: list of slots
        """
        # print("------------------------------")
        # print("Enter _query_join_objects")
        # print("------------------------------")
        logging.info("ENTER _QUERY_JOIN_OBJECTS")
    
        object_name = get_object_name(
            tracker,
            self.knowledge_base.ordinal_mention_mapping,
            self.use_last_object_mention,
        )
        
        last_object = await utils.call_potential_coroutine(
            self.knowledge_base.get_object(last_object_type, object_name)
        )
        print(last_object)

        if not object_name or not object_type or not last_object:
            dispatcher.utter_message(template="utter_ask_rephrase")
            return [SlotSet(SLOT_MENTION, None)]

        object_of_interest = await utils.call_potential_coroutine(
            self.knowledge_base.get_object(object_type, object_name)
        )

        obj_repr = self.knowledge_base.document_types[object_type].to_string(object_of_interest)
        last_obj_repr = self.knowledge_base.document_types[last_object_type].to_string(last_object)

        dispatcher.utter_message(
            text=f"{obj_repr} is the {object_type} for {last_object_type} {last_obj_repr}."
        )

        slots = [
            SlotSet(SLOT_OBJECT_TYPE, last_object_type),
            SlotSet(SLOT_MENTION, None),
            SlotSet(SLOT_ATTRIBUTE, None),
            SlotSet(SLOT_LAST_OBJECT, object_name),
            SlotSet(SLOT_LAST_OBJECT_TYPE, last_object_type)
        ]

        return slots
