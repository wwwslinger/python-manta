# Copyright (c) 2012, Joyent, Inc. All rights reserved.
#
# Makefile for python-manta
#

#
# Dirs
#
TOP := $(shell pwd)

#
# Mountain Gorilla-spec'd versioning (MG is a Joyent engineering thing).
#
# Need GNU awk for multi-char arg to "-F".
_AWK := $(shell (which gawk >/dev/null && echo gawk) \
	|| (which nawk >/dev/null && echo nawk) \
	|| echo awk)
BRANCH := $(shell git symbolic-ref HEAD | $(_AWK) -F/ '{print $$3}')
ifeq ($(TIMESTAMP),)
	TIMESTAMP := $(shell date -u "+%Y%m%dT%H%M%SZ")
endif
_GITDESCRIBE := g$(shell git describe --all --long --dirty | $(_AWK) -F'-g' '{print $$NF}')
STAMP := $(BRANCH)-$(TIMESTAMP)-$(_GITDESCRIBE)

#
# Vars, Tools, Files, Flags
#
NAME		:= python-manta
RELEASE_TARBALL	:= $(NAME)-$(STAMP).tgz
TMPDIR          := /var/tmp/$(STAMP)

# TODO: restdown docs
#DOC_FILES	 = index.restdown
#RESTDOWN_EXEC	?= deps/restdown/bin/restdown
#RESTDOWN	?= python $(RESTDOWN_EXEC)
#$(RESTDOWN_EXEC): | deps/restdown/.git


#
# Targets
#
.PHONY: all
all:

.PHONY: test
test:
	python test/test.py

.PHONY: testall
testall:
	python test/testall.py

# TODO: use distutils' sdist?
.PHONY: release
release: all
	mkdir -p $(TMPDIR)/$(NAME)
	cp -r \
		$(TOP)/bin \
		$(TOP)/lib \
		$(TOP)/setup.py \
		$(TOP)/README.md \
		$(TOP)/LICENSE.txt \
		$(TOP)/TODO.txt \
		$(TOP)/test \
		$(TMPDIR)/$(NAME)
	(cd $(TMPDIR) && $(TAR) -czf $(TOP)/build/$(RELEASE_TARBALL) $(NAME))
	@rm -rf $(TMPDIR)
	@echo "Created 'build/$(RELEASE_TARBALL)'."