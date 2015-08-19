from datetime import timedelta
from copy import deepcopy

from Ganga.GPIDev.Base.Objects import GangaObject
from Ganga.GPIDev.Schema import Schema, Version
from Ganga.GPIDev.Base.Proxy import stripProxy

from .exceptions import CredentialsError
from .ICredentialRequirement import ICredentialRequirement

import Ganga.Utility.logging
logger = Ganga.Utility.logging.getLogger()


class CredentialStore(GangaObject):
    """
    The central management for all credentials
    
    It is not intended to store the credential objects between sessions,
    rather it will search the filesystem or create new credential files.
    
    A single instance of this class makes most sense and should be created in the bootstrap and exported.
    """

    _schema = Schema(Version(1, 0), {})
                                                                                
    _category = "credentials2"
    _name = "CredentialStore"
    _hidden = 1  # This class is hidden since we want a 'singleton' created in the bootstrap

    _exportmethods = ['__getitem__', '__iter__', 'renew', 'create']

    def __init__(self):
        super(CredentialStore, self).__init__()
        self.credentials = set()
    
    def create(self, query, create=True, check_file=False):
        """
        Create an ``ICredentialInfo`` for the query.
        
        Args:
            query (ICredentialRequirement): 
            check_file: Raise an exception if the file does not exist
            create: Create the credential file
        
        Returns:
            The newly created ICredentialInfo object
        """

        cred = stripProxy(query)._infoClass(query, check_file=check_file, create=create)
        self.credentials.add(cred)
        return cred
    
    def remove(self, credential_object):
        """
        Args:
            credential_object (ICredentialInfo):
        """

        self.credentials.remove(credential_object)
    
    def __iter__(self):
        """Allow iterating over the store directly"""
        # yield from self.credentialList #In Python 3.3
        return iter(self.credentials)

    def __getitem__(self, query):
        """
        This function will try quite hard to find and wrap any missing credential
        but will never create a new file on disk

        Args:
            query (ICredentialRequirement):

        Returns:
            A single ICredentialInfo object which matches the requirements

        Raises:
            KeyError: If it could not provide a credential
            TypeError: If query is of the wrong type
        """
        query = stripProxy(query)

        if not isinstance(query, ICredentialRequirement):
            raise TypeError('Credential store query should be of type ICredentialRequirement')

        match = self.match(query)
        if match:
            return match

        # Assemble the locations to try
        if query.location:
            location_list = [query.location]  # Just use the specified one if it exists
        else:  # Otherwise try some default places
            location_list = [query.default_location(), ':'.join([query.default_location(), query.encoded()])]

        # For each location, try wrapping the file on disk
        query = deepcopy(query)  # Make a copy so we don't edit the passed-in object
        for location in location_list:
            query.location = location
            try:
                cred = self.create(query, create=False, check_file=True)
            except IOError as e:
                logger.debug(e.strerror)
            except CredentialsError as e:
                logger.debug(str(e))
            else:
                self.credentials.add(cred)
                return cred

        raise KeyError('Matching credential [{query}] not found in store.'.format(query=query))

    def get(self, query, default=None):
        """
        Return the value for ``query`` if ``query`` is in the store, else default.
        If ``default`` is not given, it defaults to ``None``, so that this method never raises a ``KeyError``.

        Args:
            query (ICredentialRequirement):
            default (ICredentialRequirement):

        Returns:
            A single ICredentialInfo object which matches the requirements or ``default``
        """
        try:
            return self[query]
        except KeyError:
            return default
    
    def get_all_matching_type(self, query):
        """
        Returns all ``ICredentialInfo`` with the type that matches the query
        
        Args:
            query (ICredentialRequirement):
        """

        return (cred for cred in self.credentials if type(cred) == stripProxy(query)._infoClass)
    
    def matches(self, query):
        """
        Search the credentials in the store for all matches. They must match every condition exactly.
        
        Args:
            query (ICredentialRequirement): 
           
        Returns:
            iterator: An iterator of all matching objects
        """

        return (cred for cred in self.get_all_matching_type(query) if cred.check_requirements(query))
    
    def match(self, query):
        """
        Returns a single match from the store
        
        Args:
            query (ICredentialRequirement):
        
        Returns:
            ICredentialInfo: A single credential object. If more than one is found, the first is returned
        """

        matches = list(self.matches(query))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            logger.debug("More than one match...")
            # If we have a specific object and a general one. Then we ask for a general one, what should we do.
            # Does it matter since they've only asked for a general proxy? What are the use cases?
            return matches[0]  # TODO For now just return the first one... Though perhaps we should merge them or something?
        return None

    def renew(self):
        """
        Renew all credentials which are invalid or will expire soon.
        It also uses the entries in `needed_credentials` and adds and renews those
        TODO Should this function be standalone?
        """
        for cred in self.credentials:
            if not cred.is_valid() or cred.time_left() < timedelta(hours=1):
                cred.renew()
        for cred_req in needed_credentials - self.credentials:
            try:
                self[cred_req].renew()
            except KeyError:
                self.create(cred_req)

# This is a global 'singleton'
credential_store = CredentialStore()

needed_credentials = set()  # type: Set[ICredentialRequirement]


def get_needed_credentials():
    # Filter out any credentials which are valid
    now_valid_creds = set()
    for cred_req in needed_credentials:
        cred = credential_store.get(cred_req)
        if cred and cred.is_valid():
            now_valid_creds.add(cred_req)

    # Remove the valid credentials from needed_credentials
    needed_credentials.difference_update(now_valid_creds)

    return needed_credentials