#!/usr/bin/env python
# -*- coding: utf-8 -*-  

################################################################################
#
# Copyright (c) 2015 Baidu.com, Inc. All Rights Reserved
#
################################################################################
"""
Authors: zhousongsong(doublesongsong@gmail.com)
Date:    2015/10/13 14:50:06
"""

import os
import sys
import threading
import Queue
import cPickle

import Target
import BrocObject

broc_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, broc_dir)
from util import Function


class BrocObjectMaster(threading.Thread):
    """
    cache Manager class
    BrocObjectMaster object is a thread object
    """
    def __init__(self, cache_file, root, logger):
        """
        Args:
            cache_file : the path of cache file
            root : the root path of main module
            logger : the Log.Log() object
        """
        threading.Thread.__init__(self)
        self._cache_file = cache_file
        self._root = root
        self._logger = logger
        self._queue = Queue.Queue()  # request queue
        self._version = 0.1
        self._cache = dict()        # {cvs path : BrocObject} 
        self._changed_cache = set() # set(BrocObject)
        self._event = threading.Event()
        self._dumped_str = ""
    
    def WaitCheckDone(self):
        """
        wait all cache has been check
        """
        self._queue.put(('check_done', None))
        # wait all target has been checked
        self._event.wait()
        self._event.clear()

    def Stop(self):
        """
        stop thread
        """
        self._queue.put(('stop', None))
        self.join()

    def SelfCheck(self):
        '''
        After BrocObjectMaster loading caches, to check whether all caches have been modified,
        this step must be execute before run BROC files
        '''
        missing = list()
        for cvs, cache in self._cache.iteritems():
            ret = cache.IsModified()
            # not change
            if ret == 0:
                continue
            # changed
            elif ret == 1:
                cache.EnableBuildNoReverse()
                continue
            # the file that cache representing is missing
            else:
                missing.append(cvs)

        # here to handle the caches of missed files
        try:
            for miss in  missing:
                del self._cache[miss]
        except KeyError:
            pass

        #for k in self._cache:
        #   print('(%s): %s' % (self._cache[k].pathname, self._cache[k].build))


    def IsModified(self, outpath):
        """
        whether a file has been modified since last build
        Args:
            outpath : the build result file
        """
        if outpath not in self._cache:
            return True
        else:
            return self._cache[outpath].Modified()

    def run(self):
        """
        Returns:
        """
        while True:
            action, obj = self._queue.get()
            if action == 'check':
                self._handle_check(obj)
                continue
            elif action == 'update':
                self._handle_update(obj)
                continue
            elif action == 'check_done':     
                self._handle_check_done()
                continue
            elif action == 'stop':
                break

    def _handle_check(self, obj):
        """
        used by BrocObjectMaster thread
        to check whether cache is changed
        Args:
            obj : target.Target object
        """
        self._check_target(obj)

    def _check_head_cache(self, pathname, source_cache):
        """
        to check whether head file changed
        if head cache does not exist, create a new head cache
        Args:
            pathname : the cvs path of head file
            source_cache : the BrocObject.SourceCache
        Returns:
            return True if head cache changed or create a new head cache
            return False if head cache didn't change
        """
        if pathname not in self._cache:
            self._add_head_cache(pathname, source_cache)
            return True

        if self._cache[pathname].IsChanged(None):
            self._cache[pathname].EnableBuild()
            return True
        else:
            return False

    def _check_source_cache(self, source, target_cache):
        """
        to check source object's cache
        Args:
            source : the Source.Source object
            target_cache : the BrocObject.TargetCache object
        """
        # source infile no exists in cache
        if source.OutFile() not in self._cache:
            source.CalcHeaderFiles()
            self._add_source_cache(source, target_cache)
            return True

        # check header files
        source_cache = self._cache[source.OutFile()]
        last_headers = set(map(lambda x: x.Pathname(), source_cache.Deps()))
        # source file content changed
        if source_cache.Modified():
            source_cache.UpdateBuildCmd(source.GetBuildCmd())
            source_cache.EnableBuild()
        else:
            if source.GetBuildCmd() != source_cache.BuildCmd():
                source_cache.UpdateBuildCmd(source.GetBuildCmd())
                source_cache.EnableBuild()
            source.SetHeaderFiles(last_headers)
             
        now_headers = source.GetHeaderFiles()
        missing_headers = last_headers - now_headers
        for f in missing_headers:
            source_cache.DelDep(f)
            self._cache[f].DelReverseDep(source_cache.Pathname())

        # check head files source object depended
        ret = False
        for f in source.GetHeaderFiles():
            if self._check_head_cache(f, source_cache):
                ret = True

        # head files changed
        if ret:
            self._cache[source.OutFile()].UpdateBuildCmd(source.GetBuildCmd())
            self._cache[source.OutFile()].EnableBuild()
            return ret

        # head files no changed, check itself
        if source_cache.IsChanged(source):
            source_cache.UpdateBuildCmd(source.GetBuildCmd())
            source_cache.EnableBuild()
            return True

        return False

    def _check_target(self, target):
        """
        to check target cache
        Args:
            target : can be Application, StaticLibrary, UT_Application, ProtoLibrary
        """
        ret = False
        # 1. check whether target cache exists
        if target.OutFile() not in self._cache:
            #self._logger.LevPrint("MSG", "create cache for target %s" % target.OutFile())
            self._add_target_cache(target)
            return True

        # 2. check whether target cache is a empty cache, empty cache was created by target depended on it
        # self._logger.LevPrint("MSG", "check target %s cache" % target.OutFile())
        target_cache = self._cache[target.OutFile()]
        if not target_cache.initialized:
            # self._logger.LevPrint("MSG", "Initialize target %s" % target.OutFile())
            target_cache.Initialize(target)
            target_cache.EnableBuild()

        # 3. check all source object, remove uesless source cache
        #self._logger.LevPrint("MSG", "check target %s Source" % target.OutFile())
        last_sources = set()
        for x in target_cache.Deps():
            if x.TYPE is BrocObject.BrocObjectType.BROC_SOURCE:
                last_sources.add(x.Pathname())
        now_sources = target.Objects()
        missing_sources = last_sources - now_sources
        for missing in missing_sources:
            target_cache.DelDep(missing)
            self._cache[missing].DelReverseDep(target.OutFile())
        # check source objets contained in trget object
        for source in target.Sources():
            if self._check_source_cache(source, target_cache):
                ret = True

        # 4. check all .a files, remove useless .a cache first
        last_libs = set()
        for x in target_cache.Deps():
            if x.TYPE is BrocObject.BrocObjectType.BROC_LIB:
                last_libs.add(x.Pathname())
        now_lib_files = target.Libs()
        missing_libs = last_libs - now_lib_files
        for missing in missing_libs:
            target_cache.DelDep(missing)
            self._cache[missing].DelReverseDep(target.OutFile())
        # self._logger.LevPrint('MSG', "check %s ..." % target.OutFile())
        # check .a files contained in target object
        for lib_file in target.Libs():
            if self._check_lib_cache(lib_file, target_cache):
                # self._logger.LevPrint("MSG", "check dep lib %s changed, enable target %s" % (lib_file, target.OutFile()))
                ret = True

        # if there is source or .a has changed, tareget need to rebuild
        if ret:
            target_cache.EnableBuild()
            # self._logger.LevPrint("MSG", 'some deps change, target %s nee to rebuild' % target.OutFile())
            return True

        # 5. check target file itself
        if target_cache.IsChanged(target):
            target_cache.EnableBuild()
            return True

        return False

    def _check_lib_cache(self, pathname, target_cache):
        """
        check lib cache
        a target(.exe, .a) can depend on static library(.a) files
        when add cache object for the target, we need to create the cache object for all dependent .a files at same time.
        In the condition, the informatin of dependent file(.a) we have is just the cvs path, so creates a empty target cache
        and the dependent relation firstly, and then initiailze it when it comes to check the true dependengt object
        Args:
            pathname: the cvs path of .a file
            target_cache : the reversed dependent target cache of lib file
        """
        
        if pathname not in self._cache:
            self._add_lib_cache(pathname, target_cache)
            return True
        # BrocObject object will check whether dep or reverse dep existed already,
        # there is no need to check, it doesn't matter, just add it
        self._cache[pathname].AddReverseDep(target_cache)
        target_cache.AddDep(self._cache[pathname]) 

    def _add_source_cache(self, source, target_cache):
        """
        add a new source cache, and create header cache
        Args:
            source : the Source.Source object
            target_cache : the BrocObject object that dependeds on the source file
        """
        # self._logger.LevPrint('MSG', 'add source cache %s' % source.InFile())
        source_cache = BrocObject.SourceCache(source)
        self._cache[source.OutFile()] = source_cache
        source_cache.AddReverseDep(target_cache)
        target_cache.AddDep(source_cache)

        # add header cache for source cache
        header_files = source.GetHeaderFiles()
        for f in header_files:
            if f in self._cache:
                source_cache.AddDep(self._cache[f])
                self._cache[f].AddReverseDep(source_cache)
            else:
                self._add_head_cache(f, source_cache)

    def _add_target_cache(self, target):
        """
        add a new target cache(lib cache, (ut)app cache)
        Args:
            target : Target.Target object
        """
        # self._logger.LevPrint("MSG", "add target cache(%s), type is (%s)" % (target.OutFile(), type(target)))
        target_cache = None
        if isinstance(target, Target.StaticLibrary):
            target_cache = BrocObject.LibCache(target.OutFile(), target)
        elif isinstance(target, Target.UTApplication) or isinstance(target, Target.Application):
            target_cache = BrocObject.AppCache(target)
        elif isinstance(target, Target.ProtoLibrary):
            target_cache = BrocObject.LibCache(target.OutFile(), target)
        else:
            self._logger.LevPrint("ERROR", "can't add target cache(%s)" % target.OutFile())
            return 

        self._cache[target.OutFile()] = target_cache
        # handle source object
        for source in target.Sources():
            if source.OutFile() in self._cache:
                self._cache[source.OutFile()].AddReverseDep(target_cache)
                target_cache.AddDep(self._cache[source.OutFile()])
                self._check_source_cache(source, target_cache)
            else:
                self._add_source_cache(source, target_cache)

        # handle dependent lib cache
        for lib in target.Libs():
            if lib in self._cache:
                self._cache[lib].AddReverseDep(target_cache)
                target_cache.AddDep(self._cache[lib])
            else:
                # add empty dependent lib cache object
                # this cache object need to be initialized 
                self._add_lib_cache(lib, target_cache)

    def _add_lib_cache(self, pathname, target_cache):
        """
        add empty lib cache
        Args:
            pathname : the cvspath of .a file
            target_cache : the cache of target depending on .a file
        """
        depend_cache = BrocObject.LibCache(pathname, target_cache, False)
        self._cache[pathname] = depend_cache 
        depend_cache.AddReverseDep(target_cache)
        target_cache.AddDep(depend_cache)

    def _add_head_cache(self, pathname, source_cache):
        """
        add head file cache
        Args:
            pathname : the cvs path of head file
            source_cache : the BrocObject.SourceCache object
        """
        cache = BrocObject.HeaderCache(pathname)
        self._cache[pathname] = cache
        source_cache.AddDep(cache)
        cache.AddReverseDep(source_cache)
        
    def CheckCache(self, obj):
        """
        to check whether cache(cvspath) is changed
        Args:
            obj : can be target, source object
        """
        self._queue.put(('check', obj))

    def _handle_check_done(self):
        """
        find all changed cache whose type in [BROC_SOURCE, BROC_LIB, BROC_APP]
        """
        for k, cache in self._cache.iteritems():
            if not cache.IsBuilt() and cache.TYPE in [BrocObject.BrocObjectType.BROC_SOURCE,
                                                      BrocObject.BrocObjectType.BROC_LIB,
                                                      BrocObject.BrocObjectType.BROC_APP]:
                self._changed_cache.add(cache)
        self._event.set()

    def UpdateCache(self, pathname):
        """
        update cache whose key is pathname, this method is used after build
        Args:
           pathname : the cvs path of file 
        """
        self._queue.put(('update', pathname))

    def _handle_update(self, pathname):
        """
        update cache whose key is pathname, this method is used after build
        Args:
           pathname : the cvs path of file 
        """
        # self._logger.LevPrint("MSG", "save cache %s" % pathname)
        if pathname not in self._cache:
            self._logger.LevPrint("INFO", "%s not in cache, could not update" % pathname)
            return 
        cache = self._cache[pathname]
        # head file information has been updated in check stage, so no need to update
        if cache.TYPE == BrocObject.BrocObjectType.BROC_HEADER:
            cache.DisableBuild()
            cache.DisableModified()
            return
        else:
            # self._logger.LevPrint("MSG", "update cache %s, hash is %s" % (cache.Pathname(), cache.Hash()))
            cache.Update()
            # save cache into file 
            # self._logger.LevPrint("MSG", "save cache %s, id(%s), hash is %s, build %s" % (cache.Pathname(), id(cache), cache.Hash(), cache.build ))
            self._save_cache()

    def GetChangedCache(self):
        """
        return the list of changed file
        Returns:
            return a list object composed of cvspath
        """
        return self._changed_cache 

    def LoadCache(self):
        """
        load cache
        """
        # no cache file
        if not os.path.exists(self._cache_file):
            self._logger.LevPrint("MSG", "no broc cache and create a empty one")
            return 
        # try to load cache file
        self._logger.LevPrint("MSG", "loading cache(%s) ..." % self._cache_file)
        try:
            with open(self._cache_file, 'rb') as f:
                caches = cPickle.load(f)
                if caches[0] != self._version:
                    self._logger.LevPrint("MSG", "cache version(%s) no match system(%s)" 
                                          % (caches[0], self._version))
                else:
                    for cache in caches[1:]:
                        self._cache[cache.Pathname()] = cache
                        #self._logger.LevPrint("MSG", 'cache %s , %d hash is %s, build %s, Modified %s' % (cache.Pathname(), id(cache), cache.Hash(), cache.Build(), cache.Modified()))
        except BaseException as err:
            self._logger.LevPrint("MSG", "load broc cache(%s) faild(%s), create a empty cache"
                                 % (self._cache_file, str(err)))
        self._logger.LevPrint("MSG", "loading cache success")
        self._logger.LevPrint("MSG", "checking cache ...")
        self.SelfCheck()
        self._logger.LevPrint("MSG", "checking cache done")

    def _save_cache(self):
        """
        save cache objects into file
        and content of file is a list and its format is [ version, cache, cache, ...].
        the first item is cache version, and the 2th, 3th ... item is cache object
        """
        dir_name = os.path.dirname(self._cache_file)
        Function.Mkdir(dir_name)
        try:
            caches = [self._version]
            caches.extend(map(lambda x: self._cache[x], self._cache))
            with open(self._cache_file, 'wb') as f:
                cPickle.dump(caches, f)
        except Exception as err:
            self._logger.LevPrint("ERROR", "save cache(%s) failed(%s)" 
                                  % (self._cache_file, str(err)))

    def _dump(self, pathname, level):
        """
        dump dependecy relationship into file, DFS
        """
        if self._cache[pathname].build is True:
            infos = "\t" * level + "[" + pathname + "]\n"          #need to build
        else:
            infos = "\t" * level + pathname + "\n"
        self._dumped_str += infos
        for deps_pathname in self._cache[pathname].deps:
            self._dump(deps_pathname.Pathname(), level + 1)

    def Dump(self):
        """
        save dependency relation of files
        """
        dumped_file = os.path.join(self._root, ".BROC.FILE.DEPS")
        for pathname in self._cache:
            # the length of reverse deps is 0 means it is application or libs of main module
            if len(self._cache[pathname].reverse_deps) <= 0:
                self._dump(pathname, 0)
        try:
            dir_name = os.path.dirname(dumped_file)
            Function.Mkdir(dir_name)
            with open(dumped_file, "w") as f:
                f.write("" + self._dumped_str)
        except IOError as err:
            self._logger.LevPrint("ERROR", "save file dependency failed(%s)" % err)
