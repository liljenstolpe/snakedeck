#!/usr/bin/env python

import logging
import os
import subprocess
import sys
import time
import threading
import yaml

from PIL import Image, ImageFont, ImageDraw
from StreamDeck.DeviceManager import DeviceManager
from StreamDeck.ImageHelpers import PILHelper

from plugins import countdown
from plugins import obs


logging.basicConfig(level=logging.DEBUG)

# Set a couple of directory paths for later use.
# This follows the spec at the following address:
# https://specifications.freedesktop.org/basedir-spec/basedir-spec-latest.html
xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
if not xdg_config_home:
  xdg_config_home = os.path.join(os.environ.get("HOME"), ".config")
config_dir = os.path.join(xdg_config_home, "snakedeck")

xdg_state_home = os.environ.get("XDG_STATE_HOME")
if not xdg_state_home:
  xdg_state_home = os.path.join(os.environ.get("HOME"), ".local", "state")
state_dir = os.path.join(xdg_state_home, "snakedec")

# Associates deck id to deck
decks = {}


# Global font for now
font = ImageFont.truetype("DroidSans", 20)


deviceManager = DeviceManager()
def deck_detector():
  while True:
    # First, let's check if any deck was disconnected.
    # Get a list of serials so that we don't change the dict while we iterate on it.
    deck_ids = list(decks)
    for deck_id in deck_ids:
      if not decks[deck_id].deck.connected():
        logging.warning(f"Deck {deck_id} ({decks[deck_id].serial_number}) was disconnected.")
        del decks[deck_id]
    # OK, now let's check if new decks are detected.
    for deck in deviceManager.enumerate():
      deck_id = deck.id()
      if deck_id not in decks:
        logging.info(f"Deck {deck_id} was detected.")
        decks[deck_id] = Deck(deck)
    time.sleep(1)


class Deck(object):

  def __init__(self, deck):
    self.deck = deck
    self.keys = {}
    self.config_timestamp = None
    logging.debug(f"Opening deck {deck.id()}.")
    self.deck.open()
    self.serial_number = self.deck.get_serial_number()
    logging.debug(f"Deck {deck.id()} is a {self.deck.DECK_TYPE}, serial number {self.serial_number}.")
    self.clear()
    self.image_size = self.deck.key_image_format()['size']
    self.config_file_path = os.path.join(config_dir, self.serial_number+".yaml")
    self.load_config()
    threading.Thread(target=self.watch_config).start()
    self.deck.set_key_callback(self.callback)

  def callback(self, deck, key, state):
    pressed_or_released = "pressed" if state else "released"
    logging.debug(f"Deck {self.serial_number} key {key} is now {pressed_or_released}.")
    try:
      key = self.keys[key]
      if state and "shell" in key:
        command = key["shell"]
        kwargs = {"shell": True}
        if "cd" in key:
          kwargs["cwd"] = key["cd"]
        ret = subprocess.call(command, **kwargs)
        if ret != 0:
          logging.warning(f"Command {command!r} exited with non-zero status code.")
      if state and "eval" in key:
        eval(key["eval"])
    except Exception as e:
      logging.exception(f"Deck {self.serial_number} key {key} caused exception {e}:")

  def clear(self):
    # Clear all keys
    self.deck.reset()
    for key in range(self.deck.KEY_COUNT):
      self.deck.set_key_image(key, self.deck.BLANK_KEY_IMAGE)
    self.deck.set_brightness(80)
    self.keys.clear()

  def load_config(self):
    if not os.path.isfile(self.config_file_path):
      logging.warning(f"Deck {self.serial_number} has no configuration file ({self.config_file_path}).")
      return
    self.config_timestamp = os.stat(self.config_file_path).st_mtime
    config = yaml.safe_load(open(self.config_file_path))

    for key in config:
      if "line" in key and "column" in key:
        # FIXME validate line/column
        key_number = (key["line"] - 1) * self.deck.KEY_COLS + key["column"] - 1
        self.keys[key_number] = key
        image = Image.new("RGB", self.image_size)
        draw = ImageDraw.Draw(image)
        text = key["label"]
        text_size = font.getsize(text)
        x = (self.image_size[0] - text_size[0]) / 2
        y = (self.image_size[1] - text_size[1]) / 2
        draw.text((x, y), text, font=font)
        deck_image = PILHelper.to_native_format(self.deck, image)
        self.deck.set_key_image(key_number, deck_image)
      else:
        if "PATH" in key:
          os.environ["PATH"] = key["PATH"] + ":" + os.environ["PATH"]

  def watch_config(self):
    while True:
      if os.path.isfile(self.config_file_path):
        if os.stat(self.config_file_path).st_mtime > self.config_timestamp:
          logging.info("Configuration file for deck {self.serial_number} changed, reloading it.")
          self.clear()
          self.load_config()


threading.Thread(target=deck_detector).start()


for t in threading.enumerate():
  if t is threading.currentThread():
    continue

  if t.is_alive():
    try:
      t.join()
    except KeyboardInterrupt:
      os._exit(0)
