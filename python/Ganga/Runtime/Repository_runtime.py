"""
Internal initialization of the repositories.
"""

import Ganga.Utility.Config
from Ganga.Utility.logging import getLogger
import os.path
from Ganga.Utility.files import expandfilename, fullpath
from Ganga.Core.GangaRepository import getRegistries
from Ganga.Core.GangaRepository import getRegistry
from Ganga.Core.exceptions import GangaException

config = Ganga.Utility.Config.getConfig('Configuration')
logger = getLogger()

_runtime_interface = None

def requiresAfsToken():
    # Were we executed from within an AFS folder
    return fullpath(getLocalRoot(), True).find('/afs') == 0


def getLocalRoot():
    # Get the local top level directory for the Repo
    if config['repositorytype'] in ['LocalXML', 'LocalAMGA', 'LocalPickle', 'SQLite']:
        return os.path.join(expandfilename(config['gangadir'], True), 'repository', config['user'], config['repositorytype'])
    else:
        return ''

def getLocalWorkspace():
    # Get the local top level dirtectory for the Workspace
    if config['repositorytype'] in ['LocalXML', 'LocalAMGA', 'LocalPickle', 'SQLite']:
        return os.path.join(expandfilename(config['gangadir'], True), 'workspace', config['user'], config['repositorytype'])
    else:
        return ''


started_registries = []

partition_warning = 95
partition_critical = 99

def checkDiskQuota():
    # Throw an error atthe user if their AFS area is (extremely close to) full to avoid repo corruption
    import subprocess

    repo_partition = getLocalRoot()
    repo_partition = os.path.realpath(repo_partition)

    work_partition = getLocalWorkspace()
    work_partition = os.path.realpath(work_partition)

    folders_to_check = [repo_partition, work_partition]

    home_dir = os.environ['HOME']
    to_remove = []
    for partition in folders_to_check:
        if not os.path.exists(partition):
            if home_dir not in folders_to_check:
                folders_to_check.append(home_dir)
            to_remove.append(partition)

    for folder in to_remove:
        folders_to_check.remove(folder)

    for data_partition in folders_to_check:

        if fullpath(data_partition, True).find('/afs') == 0:
            quota = subprocess.Popen(['fs', 'quota', '%s' % data_partition], stdout=subprocess.PIPE)
            output = quota.communicate()[0]
            logger.debug("fs quota %s:\t%s" % (str(data_partition), str(output)))
        else:
            df = subprocess.Popen(["df", '-Pk', data_partition], stdout=subprocess.PIPE)
            output = df.communicate()[0]

        try:
            global partition_warning
            global partition_critical
            quota_percent = output.split('%')[0]
            if int(quota_percent) >= partition_warning:
                logger.warning("WARNING: You're running low on disk space, Ganga may stall on launch or fail to download job output")
                logger.warning("WARNING: Please free some disk space on: %s" % str(data_partition))
            if int(quota_percent) >= partition_critical and config['force_start'] is False:
                logger.error("You are crtitically low on disk space!")
                logger.error("To prevent repository corruption and data loss we won't start Ganga.")
                logger.error("Either set your config variable 'force_start' in .gangarc to enable starting and ignore this check.")
                logger.error("Or, make sure you have more than %s percent free disk space on: %s" %(str(100-partition_critical), str(data_partition)))
                raise GangaException("Not Enough Disk Space!!!")
        except GangaException as err:
            raise err
        except Exception as err:
            logger.debug("Error checking disk partition: %s" % str(err))

    return

def bootstrap_getreg():
    # Get the list of registries sorted in the bootstrap way
    
    # ALEX added this as need to ensure that prep registry is started up BEFORE job or template
    # or even named templated registries as the _auto__init from job will require the prep registry to
    # already be ready. This showed up when adding the named templates.
    def prep_filter(x, y):
        if x.name == 'prep':
            return -1
        return 1

    return [registry for registry in sorted(getRegistries(), prep_filter)]

def bootstrap_reg_names():
    # Get the list of registry names
    all_reg = bootstrap_getreg()
    return [reg.name for reg in all_reg]

def bootstrap():
    # Bootstrap for startup and setting of parameters for the Registries
    retval = []

    try:
        checkDiskQuota()
    except GangaException as err:
        raise err
    except Exception as err:
        logger.error("Disk quota check failed due to: %s" % str(err))

    for registry in bootstrap_getreg():
        if registry.name in started_registries:
            continue
        if not hasattr(registry, 'type'):
            registry.type = config["repositorytype"]
        if not hasattr(registry, 'location'):
            registry.location = getLocalRoot()
        logger.debug("Registry: %s" % registry.name)
        logger.debug("Loc: %s" % registry.location)
        registry.startup()
        logger.debug("started " + registry.info(full=False))
        if registry.name == "prep":
            registry.print_other_sessions()
        started_registries.append(registry.name)
        retval.append((registry.name, registry.getProxy(), registry.doc))

    #import atexit
    #atexit.register(shutdown)
    #logger.debug("Registries: %s" % str(started_registries))
    return retval


def updateLocksNow():
    # Update all of the file locks for the registries

    logger.debug("Updating timestamp of Lock files")
    for registry in getRegistries():
        registry.updateLocksNow()
    return


def shutdown():
    # Shutdown method for all repgistries in order
    from Ganga.Utility.logging import getLogger
    logger = getLogger()
    logger.info('Registry Shutdown')
    #import traceback
    #traceback.print_stack()
    # shutting down the prep registry (i.e. shareref table) first is necessary to allow the closedown()
    # method to perform actions on the box and/or job registries.
    logger.debug(started_registries)

    all_registries = getRegistries()

    try:
        if 'prep' in started_registries:
            registry = getRegistry('prep')
            registry.shutdown()
            # in case this is called repeatedly, only call shutdown once
            started_registries.remove(registry.name)
    except Exception as err:
        logger.debug("Err: %s" % str(err))
        logger.error("Failed to Shutdown prep Repository!!! please check for stale lock files")
        logger.error("Trying to shutdown cleanly regardless")

    for registry in getRegistries():
        thisName = registry.name
        try:
            if not thisName in started_registries:
                continue
            # in case this is called repeatedly, only call shutdown once
            started_registries.remove(thisName)
            registry.shutdown()  # flush and release locks
        except Exception as x:
            logger.error("Failed to Shutdown Repository: %s !!! please check for stale lock files" % thisName)
            logger.error("%s" % str(x))
            logger.error("Trying to Shutdown cleanly regardless")


    for registry in all_registries:

        my_reg = [registry]
        if hasattr(registry, 'metadata'):
            if registry.metadata:
                my_reg.append(registry.metadata)

        assigned_attrs = ['location', 'type']
        for this_reg in my_reg:
            for attr in assigned_attrs:
                if hasattr(registry, attr):
                    delattr(registry, attr)

    from Ganga.Core.GangaRepository.SessionLock import removeGlobalSessionFiles, removeGlobalSessionFileHandlers
    removeGlobalSessionFileHandlers()
    removeGlobalSessionFiles()

    removeRegistries()

def flush_all():
    # Flush all registries in their current state with all dirty knowledge going to disk
    from Ganga.Utility.logging import getLogger
    logger = getLogger()
    logger.debug("Flushing All repositories")

    for registry in getRegistries():
        thisName = registry.name
        try:
            if registry.hasStarted() is True:
                logger.debug("Flushing: %s" % thisName)
                registry.flush_all()
        except Exception as err:
            logger.debug("Failed to flush: %s" % str(thisName))
            logger.debug("Err: %s" % str(err))


def startUpRegistries(my_interface=None):
    # Startup the registries and export them to the GPI, also add jobtree and shareref
    from Ganga.Runtime.GPIexport import exportToInterface
    if not my_interface:
        import Ganga.GPI
        my_interface = Ganga.GPI
    # import default runtime modules

    global _runtime_interface
    _runtime_interface = my_interface

    # bootstrap user-defined runtime modules and enable transient named
    # template registries

    # bootstrap runtime modules
    from Ganga.GPIDev.Lib.JobTree import TreeError

    for n, k, d in bootstrap():
        # make all repository proxies visible in GPI
        exportToInterface(my_interface, n, k, 'Objects', d)

    # JobTree
    from Ganga.Core.GangaRepository import getRegistry
    jobtree = getRegistry("jobs").getJobTree()
    exportToInterface(my_interface, 'jobtree', jobtree, 'Objects', 'Logical tree view of the jobs')
    exportToInterface(my_interface, 'TreeError', TreeError, 'Exceptions')

    # ShareRef
    shareref = getRegistry("prep").getShareRef()
    exportToInterface(my_interface, 'shareref', shareref, 'Objects', 'Mechanism for tracking use of shared directory resources')

def removeRegistries():
    ## Remove lingering Objects from the GPI and fully cleanup after the startup

    ## First start with repositories

    import Ganga.GPI

    from Ganga.Runtime import Repository_runtime

    to_remove = Repository_runtime.bootstrap_reg_names()
    to_remove.append('jobtree')
    to_remove.append('shareref')

    global _runtime_interface
    for name in to_remove:
        if hasattr(_runtime_interface, name):
            delattr(_runtime_interface, name)

    _runtime_interface = None

