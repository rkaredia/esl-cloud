from django.core.files.storage import FileSystemStorage
import os

class OverwriteStorage(FileSystemStorage):
    """
    Custom storage class that deletes the existing file 
    if a file with the same name is uploaded.
    """
    def get_available_name(self, name, max_length=None):
        # If the filename already exists in the media folder, delete it
        if self.exists(name):
            self.delete(name)
        return name