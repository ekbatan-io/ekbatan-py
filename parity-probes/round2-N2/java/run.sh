#!/bin/zsh
# Area N2: compile the builder probes with the real annotation processor, then run.
W=/private/tmp/claude-501/-Users-mike-workspace-ekbatan/73e3c410-5a5d-4b46-86a8-40a1e2b99616/scratchpad/r2java-N2
J=/Users/mike/.sdkman/candidates/java/25.0.3-graal/bin
D=${0:A:h}
CORE=$W/ekbatan-core/build/libs/ekbatan-core-1.1.2.jar
AP=$W/ekbatan-annotation-processor/build/libs/ekbatan-annotation-processor-1.1.2.jar
PROC=$AP:$(find /Users/mike/.gradle/caches/modules-2 -name 'javapoet-1.13.0.jar' | head -1)
rm -rf $D/out $D/gen && mkdir -p $D/out $D/gen $D/out2 $D/gen2
$J/javac -cp $CORE:$AP -processorpath $PROC -s $D/gen -d $D/out $D/src/nprobe/BuilderProbe.java || exit 1
$J/java -cp $CORE:$D/out nprobe.BuilderProbe
echo "--- subclass compile"
$J/javac -cp $CORE:$AP -processorpath $PROC -s $D/gen2 -d $D/out2 $D/src2/nprobe2/SubclassProbe.java 2>&1 | head -8
