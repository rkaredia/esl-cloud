from django.core.files.storage import FileSystemStorage
import os

"""
FILE SYSTEM CUSTOMIZATION
--------------------------
Django's default behavior for file uploads is 'Sequential Renaming'.
If you upload 'tag1.bmp' and then upload it again, Django saves the
second one as 'tag1_1.bmp' to prevent data loss.

For ESL Tags, we WANT to overwrite the old image. The MAC address
of the tag is the unique identifier, and we only ever need the
LATEST image for that tag.

This class overrides the default logic to ensure we don't fill up
the disk with thousands of old, redundant BMP files.
"""

class OverwriteStorage(FileSystemStorage):
    """
    SAIS OVERWRITE STORAGE
    ----------------------
    Forces the file system to replace existing files of the same name.
    Used by the ESLTag.tag_image field.
    """

    def get_available_name(self, name, max_length=None):
        """
        EDUCATIONAL: This method is called by Django just before it
        saves a file to determine the 'final' filename.
        """
        # If the filename already exists in the media folder:
        if self.exists(name):
            # Delete the old file first
            self.delete(name)

        # Return the original name so Django uses it exactly
        return name
