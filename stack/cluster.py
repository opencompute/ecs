from troposphere import (
    AWS_REGION,
    AWS_STACK_ID,
    AWS_STACK_NAME,
    autoscaling,
    Base64,
    cloudformation,
    elasticloadbalancing as elb,
    FindInMap,
    GetAtt,
    iam,
    Join,
    Output,
    Parameter,
    Ref,
)

from troposphere.ec2 import (
    SecurityGroup,
    SecurityGroupRule,
)

from troposphere.ecs import (
    Cluster,
)

from .template import template
from .vpc import (
    vpc,
    loadbalancer_a_subnet,
    loadbalancer_b_subnet,
)


certificate_id = Ref(template.add_parameter(Parameter(
    "CertId",
    Description="Web SSL certificate id",
    Type="String",
)))


container_instance_type = Ref(template.add_parameter(Parameter(
    "ContainerInstanceType",
    Description="The container instance type",
    Type="String",
    Default="t2.micro",
    AllowedValues=["t2.micro", "t2.small", "t2.medium"]
)))


web_worker_port = Ref(template.add_parameter(Parameter(
    "WebWorkerPort",
    Description="Web worker container exposed port",
    Type="Number",
    Default="8000",
)))


template.add_mapping("ECSRegionMap", {
    "eu-west-1": {"AMI": "ami-4e6ffe3d"},
    "us-east-1": {"AMI": "ami-8f7687e2"},
    "us-west-2": {"AMI": "ami-84b44de4"},
})


# Web load balancer
load_balancer_security_group = SecurityGroup(
    "LoadBalancerSecurityGroup",
    template=template,
    GroupDescription="Web load balancer security group.",
    VpcId=Ref(vpc),
    SecurityGroupIngress=[
        SecurityGroupRule(
            IpProtocol="tcp",
            FromPort="443",
            ToPort="443",
            CidrIp='0.0.0.0/0',
        ),
    ],
)

load_balancer = elb.LoadBalancer(
    'LoadBalancer',
    template=template,
    Subnets=[
        Ref(loadbalancer_a_subnet),
        Ref(loadbalancer_b_subnet),
    ],
    SecurityGroups=[Ref(load_balancer_security_group)],
    Listeners=[elb.Listener(
        LoadBalancerPort=443,
        InstanceProtocol='HTTP',
        InstancePort=web_worker_port,
        Protocol='HTTPS',
        SSLCertificateId=certificate_id,
    )],
    CrossZone=True,
)

template.add_output(Output(
    "LoadBalancerDNSName",
    Description="Loadbalancer DNS",
    Value=GetAtt(load_balancer, "DNSName")
))


# ECS cluster
cluster = Cluster(
    "Cluster",
    template=template,
)


# ECS container role
container_instance_role = iam.Role(
    "ContainerInstanceRole",
    template=template,
    AssumeRolePolicyDocument=dict(Statement=[dict(
        Effect="Allow",
        Principal=dict(Service=["ec2.amazonaws.com"]),
        Action=["sts:AssumeRole"],
    )]),
    Path="/",
    Policies=[
        iam.Policy(
            PolicyName="ECSManagementPolicy",
            PolicyDocument=dict(
                Statement=[dict(
                    Effect="Allow",
                    Action=[
                        "ecs:*",
                    ],
                    Resource="*",
                )],
            ),
        ),
    ]
)


# ECS container instance profile
container_instance_profile = iam.InstanceProfile(
    "ContainerInstanceProfile",
    template=template,
    Path="/",
    Roles=[Ref(container_instance_role)],
)


container_instance_configuration_name = "ContainerLaunchConfiguration"


container_instance_configuration = autoscaling.LaunchConfiguration(
    container_instance_configuration_name,
    template=template,
    Metadata=autoscaling.Metadata(
        cloudformation.Init(dict(
            config=cloudformation.InitConfig(
                commands=dict(
                    register_cluster=dict(command=Join("", [
                        "#!/bin/bash\n",
                        # Register the cluster
                        "echo ECS_CLUSTER=",
                        Ref(cluster),
                        " >> /etc/ecs/config\n",
                    ]))
                ),
                files=cloudformation.InitFiles({
                    "/etc/cfn/cfn-hup.conf": cloudformation.InitFile(
                        content=Join("", [
                            "[main]\n",
                            "template=",
                            Ref(AWS_STACK_ID),
                            "\n",
                            "region=",
                            Ref(AWS_REGION),
                            "\n",
                        ]),
                        mode="000400",
                        owner="root",
                        group="root",
                    ),
                    "/etc/cfn/hooks.d/cfn-auto-reload.conf":
                    cloudformation.InitFile(
                        content=Join("", [
                            "[cfn-auto-reloader-hook]\n",
                            "triggers=post.update\n",
                            "path=Resources.%s."
                            % container_instance_configuration_name,
                            "Metadata.AWS::CloudFormation::Init\n",
                            "action=/opt/aws/bin/cfn-init -v ",
                            "         --template ",
                            Ref(AWS_STACK_NAME),
                            "         --resource %s"
                            % container_instance_configuration_name,
                            "         --region ",
                            Ref("AWS::Region"),
                            "\n",
                            "runas=root\n",
                        ])
                    )
                }),
                services=dict(
                    sysvinit=cloudformation.InitServices({
                        'cfn-hup': cloudformation.InitService(
                            enabled=True,
                            ensureRunning=True,
                            files=[
                                "/etc/cfn/cfn-hup.conf",
                                "/etc/cfn/hooks.d/cfn-auto-reloader.conf",
                            ]
                        ),
                    })
                )
            )
        ))
    ),
    InstanceType=container_instance_type,
    ImageId=FindInMap("ECSRegionMap", Ref(AWS_REGION), "AMI"),
    IamInstanceProfile=Ref(container_instance_profile),
    UserData=Base64(Join('', [
        "#!/bin/bash -xe\n",
        "yum install -y aws-cfn-bootstrap\n",

        "/opt/aws/bin/cfn-init -v ",
        "         --template ", Ref(AWS_STACK_NAME),
        "         --resource %s " % container_instance_configuration_name,
        "         --region ", Ref(AWS_REGION), "\n",
    ])),
)
